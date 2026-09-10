from __future__ import annotations

import json
import pathlib
import re
from collections import Counter, defaultdict
from typing import Any

OCR_QUALITY_VERSION = "0.1.0"
WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'-]{2,}$")


def _normalise_token(text: str) -> str:
    return text.strip(".,;:!?()[]{}\"'").lower()


def _edit_distance_limited(a: str, b: str, limit: int = 2) -> int:
    """Levenshtein distance with an early length bound.

    This is deliberately small and deterministic. It is only used to propose
    spelling candidates; it never mutates manufacturer/OCR source text.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        row_min = i
        for j, cb in enumerate(b, start=1):
            value = min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (ca != cb),
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def build_document_lexicon(
    page_payloads: list[dict[str, Any]],
    *,
    min_confidence: float = 92.0,
    min_occurrences: int = 2,
) -> dict[str, dict[str, Any]]:
    """Build a vocabulary only from repeated, high-confidence source OCR.

    The lexicon is document-derived: MEMS Forge does not invent vocabulary from
    general knowledge. That prevents an automotive/manufacturer term from being
    silently replaced merely because a generic dictionary prefers another word.
    """
    counts: Counter[str] = Counter()
    confidence_sum: defaultdict[str, float] = defaultdict(float)
    surface_forms: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for page in page_payloads:
        for word in page.get("words", []):
            text = str(word.get("text", ""))
            token = _normalise_token(text)
            confidence = word.get("confidence")
            if confidence is None or float(confidence) < min_confidence:
                continue
            if not WORD_RE.match(token):
                continue
            counts[token] += 1
            confidence_sum[token] += float(confidence)
            surface_forms[token][text] += 1

    result: dict[str, dict[str, Any]] = {}
    for token, count in counts.items():
        if count < min_occurrences:
            continue
        canonical = surface_forms[token].most_common(1)[0][0]
        result[token] = {
            "canonical": canonical,
            "occurrences": count,
            "mean_confidence": confidence_sum[token] / count,
        }
    return result


def propose_spelling_candidates(
    page_payload: dict[str, Any],
    lexicon: dict[str, dict[str, Any]],
    *,
    suspect_confidence: float = 85.0,
) -> list[dict[str, Any]]:
    """Propose likely OCR spelling corrections without applying them.

    A candidate needs strong evidence from repeated high-confidence occurrences
    elsewhere in the same source document. Ambiguous matches are retained as
    review items instead of choosing one silently.
    """
    candidates: list[dict[str, Any]] = []
    for word in page_payload.get("words", []):
        confidence = word.get("confidence")
        if confidence is None or float(confidence) >= suspect_confidence:
            continue
        original = str(word.get("text", ""))
        token = _normalise_token(original)
        if len(token) < 4 or not WORD_RE.match(token) or token in lexicon:
            continue

        max_distance = 1 if len(token) <= 7 else 2
        matches: list[tuple[int, int, float, str, dict[str, Any]]] = []
        for known, info in lexicon.items():
            if abs(len(known) - len(token)) > max_distance:
                continue
            # First character agreement sharply reduces dangerous unrelated
            # substitutions while still allowing common OCR confusions later.
            if known[0] != token[0]:
                continue
            distance = _edit_distance_limited(token, known, max_distance)
            if distance <= max_distance:
                matches.append(
                    (
                        distance,
                        -int(info["occurrences"]),
                        -float(info["mean_confidence"]),
                        known,
                        info,
                    )
                )

        if not matches:
            continue
        matches.sort()
        best = matches[0]
        tied = [m for m in matches if m[:3] == best[:3]]
        status = "REVIEW_REQUIRED" if len(tied) != 1 else "STRONG_CANDIDATE_REVIEW_REQUIRED"
        proposals = [
            {
                "normalised": match[3],
                "canonical": match[4]["canonical"],
                "edit_distance": match[0],
                "document_occurrences": match[4]["occurrences"],
                "document_mean_confidence": match[4]["mean_confidence"],
            }
            for match in tied[:5]
        ]
        candidates.append(
            {
                "status": status,
                "original": original,
                "original_confidence": float(confidence),
                "page_number": page_payload.get("page_number"),
                "bbox": [
                    int(word.get("left", 0)),
                    int(word.get("top", 0)),
                    int(word.get("left", 0)) + int(word.get("width", 0)),
                    int(word.get("top", 0)) + int(word.get("height", 0)),
                ],
                "source_word_key": [
                    int(word.get("block_num", 0)),
                    int(word.get("par_num", 0)),
                    int(word.get("line_num", 0)),
                    int(word.get("word_num", 0)),
                ],
                "proposals": proposals,
                "rule": "document_high_confidence_lexicon_edit_distance",
                "applied": False,
            }
        )
    return candidates


def audit_document_ocr_spelling(
    page_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    lexicon = build_document_lexicon(page_payloads)
    pages: list[dict[str, Any]] = []
    total_candidates = 0
    for payload in page_payloads:
        spelling = propose_spelling_candidates(payload, lexicon)
        total_candidates += len(spelling)
        low_confidence_alpha = sum(
            1
            for word in payload.get("words", [])
            if word.get("confidence") is not None
            and float(word["confidence"]) < 85.0
            and WORD_RE.match(_normalise_token(str(word.get("text", ""))))
        )
        unresolved = low_confidence_alpha > 0
        pages.append(
            {
                "page_number": payload.get("page_number"),
                "low_confidence_alpha_word_count": low_confidence_alpha,
                "spelling_candidate_count": len(spelling),
                "spelling_candidates": spelling,
                "translation_gate": "BLOCKED_OCR_REVIEW" if unresolved else "SOURCE_TEXT_READY",
            }
        )

    return {
        "ocr_quality_version": OCR_QUALITY_VERSION,
        "lexicon_size": len(lexicon),
        "spelling_candidate_count": total_candidates,
        "pages_blocked_for_translation": [
            p["page_number"] for p in pages if p["translation_gate"] == "BLOCKED_OCR_REVIEW"
        ],
        "pages": pages,
        "policy": (
            "Le texte OCR brut reste immuable. Les corrections orthographiques sont "
            "uniquement des propositions sourcées. La traduction technique ne doit "
            "pas consommer silencieusement un mot OCR douteux."
        ),
    }


def run_ocr_quality_audit(ocr_root: pathlib.Path, output_root: pathlib.Path) -> dict[str, Any]:
    ocr_root = pathlib.Path(ocr_root)
    output_root = pathlib.Path(output_root)
    page_files = sorted(ocr_root.glob("*/pages/page-*.json"))
    if not page_files:
        raise RuntimeError(f"Aucune page OCR trouvée dans {ocr_root}")

    page_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in page_files]
    result = audit_document_ocr_spelling(page_payloads)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "ocr_quality_summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result
