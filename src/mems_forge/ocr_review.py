from __future__ import annotations

import json
import math
import pathlib
import re
from typing import Any

OCR_REVIEW_VERSION = "0.1.0"
SEMANTIC_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'-]{2,}$")
PAGE_REVIEW_MEAN_CONFIDENCE = 85.0
PAGE_REVIEW_LOW_CONFIDENCE = 50.0
PAGE_REVIEW_LOW_CONFIDENCE_MIN_COUNT = 3
PAGE_REVIEW_LOW_CONFIDENCE_RATIO = 0.05
LOCALIZED_REVIEW_CONFIDENCE = 85.0


def _normalise_token(text: str) -> str:
    return str(text).strip(".,;:!?()[]{}\"'").lower()


def semantic_confidence_summary(words: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure confidence only on word-like OCR tokens.

    Raw OCR is never mutated. Punctuation, table leaders and isolated graphical
    tokens remain in the source word list but do not by themselves make the
    whole page a manual-review page.
    """
    values = [
        float(word["confidence"])
        for word in words
        if word.get("confidence") is not None
        and SEMANTIC_WORD_RE.match(_normalise_token(str(word.get("text", ""))))
    ]
    if not values:
        return {
            "count": 0,
            "mean": None,
            "below_50": 0,
            "below_70": 0,
            "below_85": 0,
            "below_50_ratio": None,
            "below_85_ratio": None,
        }

    below_50 = sum(value < PAGE_REVIEW_LOW_CONFIDENCE for value in values)
    below_85 = sum(value < LOCALIZED_REVIEW_CONFIDENCE for value in values)
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "below_50": below_50,
        "below_70": sum(value < 70.0 for value in values),
        "below_85": below_85,
        "below_50_ratio": below_50 / len(values),
        "below_85_ratio": below_85 / len(values),
    }


def page_review_reasons(page: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    words = list(page.get("words", []))
    semantic = semantic_confidence_summary(words)
    reasons: list[str] = []

    if page.get("parse_warnings"):
        reasons.append("ocr_parse_warning")
    if not words:
        reasons.append("no_ocr_words")
    elif semantic["count"] == 0:
        reasons.append("no_semantic_ocr_words")

    if semantic["mean"] is not None and semantic["mean"] < PAGE_REVIEW_MEAN_CONFIDENCE:
        reasons.append("semantic_mean_ocr_confidence_below_85")

    if semantic["count"]:
        dense_threshold = max(
            PAGE_REVIEW_LOW_CONFIDENCE_MIN_COUNT,
            math.ceil(semantic["count"] * PAGE_REVIEW_LOW_CONFIDENCE_RATIO),
        )
        if semantic["below_50"] >= dense_threshold:
            reasons.append("dense_semantic_ocr_words_below_50")

    if int(page.get("max_word_length", 0)) > 120:
        reasons.append("suspiciously_long_ocr_token")

    return reasons, semantic


def review_policy() -> dict[str, Any]:
    return {
        "ocr_review_version": OCR_REVIEW_VERSION,
        "semantic_token_pattern": SEMANTIC_WORD_RE.pattern,
        "semantic_mean_confidence_threshold": PAGE_REVIEW_MEAN_CONFIDENCE,
        "very_low_confidence_threshold": PAGE_REVIEW_LOW_CONFIDENCE,
        "dense_very_low_confidence_min_count": PAGE_REVIEW_LOW_CONFIDENCE_MIN_COUNT,
        "dense_very_low_confidence_ratio": PAGE_REVIEW_LOW_CONFIDENCE_RATIO,
        "localized_review_confidence_threshold": LOCALIZED_REVIEW_CONFIDENCE,
        "principle": (
            "Un doute OCR local reste trace et revu localement; il ne force une "
            "revue de page entiere que si les indices montrent une degradation systemique."
        ),
    }


def refine_full_ocr_review(full_audit_root: pathlib.Path) -> dict[str, Any]:
    """Refine full-page OCR review flags without altering OCR source text."""
    full_audit_root = pathlib.Path(full_audit_root)
    source_dirs = [path for path in (full_audit_root / "ocr").iterdir() if path.is_dir()]
    if len(source_dirs) != 1:
        raise RuntimeError(f"Attendu exactement un dossier OCR source, trouve: {len(source_dirs)}")

    source_dir = source_dirs[0]
    page_files = sorted((source_dir / "pages").glob("page-*.json"))
    if not page_files:
        raise RuntimeError("Aucune page OCR a raffiner")

    page_summaries: list[dict[str, Any]] = []
    for page_file in page_files:
        page = json.loads(page_file.read_text(encoding="utf-8"))
        reasons, semantic = page_review_reasons(page)
        localized_word_count = int(semantic["below_85"])

        page["ocr_review_version"] = OCR_REVIEW_VERSION
        page["semantic_confidence"] = semantic
        page["review_required"] = bool(reasons)
        page["review_reasons"] = reasons
        page["localized_review_required"] = localized_word_count > 0
        page["localized_review_word_count"] = localized_word_count
        page_file.write_text(json.dumps(page, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        page_summaries.append(
            {
                "page_number": int(page["page_number"]),
                "word_count": len(page.get("words", [])),
                "semantic_confidence": semantic,
                "review_required": bool(reasons),
                "review_reasons": reasons,
                "localized_review_required": localized_word_count > 0,
                "localized_review_word_count": localized_word_count,
            }
        )

    summary_path = source_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["ocr_review_version"] = OCR_REVIEW_VERSION
    summary["review_required_pages"] = [
        page["page_number"] for page in page_summaries if page["review_required"]
    ]
    summary["no_semantic_word_pages"] = [
        page["page_number"]
        for page in page_summaries
        if page["word_count"] > 0 and page["semantic_confidence"]["count"] == 0
    ]
    summary["localized_review_required_pages"] = [
        page["page_number"] for page in page_summaries if page["localized_review_required"]
    ]
    summary["localized_review_word_count"] = sum(
        page["localized_review_word_count"] for page in page_summaries
    )
    summary["page_review_policy"] = review_policy()
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    audit_path = full_audit_root / "audit_summary.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    ocr = audit.setdefault("ocr", {})
    ocr["ocr_review_version"] = OCR_REVIEW_VERSION
    ocr["review_required_page_count"] = len(summary["review_required_pages"])
    ocr["review_required_pages"] = summary["review_required_pages"]
    ocr["no_semantic_word_pages"] = summary["no_semantic_word_pages"]
    ocr["localized_review_page_count"] = len(summary["localized_review_required_pages"])
    ocr["localized_review_word_count"] = summary["localized_review_word_count"]
    ocr["page_review_policy"] = summary["page_review_policy"]
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "ocr_review_version": OCR_REVIEW_VERSION,
        "processed_page_count": len(page_summaries),
        "review_required_page_count": len(summary["review_required_pages"]),
        "review_required_pages": summary["review_required_pages"],
        "no_semantic_word_pages": summary["no_semantic_word_pages"],
        "localized_review_page_count": len(summary["localized_review_required_pages"]),
        "localized_review_word_count": summary["localized_review_word_count"],
        "policy": summary["page_review_policy"],
    }
