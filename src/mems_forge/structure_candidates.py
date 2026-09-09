from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict
from typing import Any

STRUCTURE_VERSION = "0.2.0"

REPAIR_NO_RE = re.compile(r"\bService\s+Repair\s+No\.?\s*([0-9]+(?:\.[0-9]+){1,4})\b", re.IGNORECASE)
STEP_RE = re.compile(r"^\s*(\d{1,3})\.\s*(.*)$")
BULLET_RE = re.compile(r"^\s*(?:[•●▪◦*-]|[eo]\s+)\s*(.*)$", re.IGNORECASE)

# Only exact source forms are accepted here. OCR-looking misspellings such as
# "dismantie" are deliberately NOT normalised silently: they remain review data.
PHASE_LABELS = {
    "remove": "remove",
    "refit": "refit",
    "refitting": "refit",  # exact manufacturer heading observed in AKM7169
    "dismantle": "dismantle",
    "reassemble": "reassemble",
    "adjust": "adjust",
    "adjustment": "adjustment",
    "check": "check",
    "inspection": "inspection",
    "overhaul": "overhaul",
}


def _normalise_space(text: str) -> str:
    return " ".join(text.replace("\u2013", "-").replace("\u2014", "-").split())


def _letters(text: str) -> str:
    return "".join(ch for ch in text if ch.isalpha())


def _looks_like_title(text: str) -> bool:
    text = _normalise_space(text)
    letters = _letters(text)
    if len(letters) < 4 or len(text) > 110:
        return False
    uppercase = sum(ch.isupper() for ch in letters)
    ratio = uppercase / max(1, len(letters))
    if ratio < 0.82:
        return False
    if text.upper() in {"CONTENTS", "INTRODUCTION", "DESCRIPTION AND OPERATION", "ADJUSTMENTS", "REPAIRS"}:
        return False
    return True


def _phase_label(text: str) -> tuple[str, str] | None:
    source_form = _normalise_space(text).strip(" :.-").lower()
    canonical = PHASE_LABELS.get(source_form)
    if canonical is None:
        return None
    return canonical, source_form


def _line_ref(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "region": line.get("region"),
        "source_line_key": line.get("source_line_key"),
        "bbox": line.get("bbox"),
        "mean_confidence": line.get("mean_confidence"),
        "text": line.get("text", ""),
    }


def _nearest_title_index(lines: list[dict[str, Any]], repair_index: int) -> int | None:
    for index in range(repair_index - 1, max(-1, repair_index - 5), -1):
        text = _normalise_space(str(lines[index].get("text", "")))
        if not text:
            continue
        if _looks_like_title(text):
            return index
    return None


def _sequence_status(numbers: list[int]) -> str:
    if not numbers:
        return "none"
    if len(numbers) == 1:
        return "single_step"
    expected = list(range(numbers[0], numbers[0] + len(numbers)))
    return "contiguous" if numbers == expected else "gap_restart_or_ocr_error"


def _finalise_phase(phase: dict[str, Any]) -> None:
    numbers = [int(step["number"]) for step in phase["steps"]]
    phase["step_numbers"] = numbers
    phase["step_sequence_status"] = _sequence_status(numbers)


def _parse_procedure_segment(
    *,
    page_number: int,
    region: str,
    lines: list[dict[str, Any]],
    title_index: int | None,
    repair_index: int,
    end_index: int,
) -> dict[str, Any] | None:
    repair_text = _normalise_space(str(lines[repair_index].get("text", "")))
    repair_match = REPAIR_NO_RE.search(repair_text)
    if not repair_match:
        return None

    title = None
    title_source = None
    if title_index is not None:
        title = _normalise_space(str(lines[title_index].get("text", "")))
        title_source = _line_ref(lines[title_index])

    phases: list[dict[str, Any]] = []
    current_phase: dict[str, Any] | None = None
    current_step: dict[str, Any] | None = None
    all_steps: list[dict[str, Any]] = []
    uncategorised_lines: list[dict[str, Any]] = []

    for line in lines[repair_index + 1 : end_index]:
        text = _normalise_space(str(line.get("text", "")))
        if not text:
            continue

        phase_match = _phase_label(text)
        if phase_match is not None:
            if current_phase is not None:
                _finalise_phase(current_phase)
            canonical_phase, source_form = phase_match
            current_phase = {
                "label": canonical_phase,
                "source_label": source_form,
                "source": _line_ref(line),
                "steps": [],
            }
            phases.append(current_phase)
            current_step = None
            continue

        step_match = STEP_RE.match(text)
        if step_match:
            number = int(step_match.group(1))
            body = step_match.group(2).strip()
            current_step = {
                "number": number,
                "text": body,
                "phase": current_phase["label"] if current_phase else None,
                "source_lines": [_line_ref(line)],
                "subitems": [],
            }
            all_steps.append(current_step)
            if current_phase is not None:
                current_phase["steps"].append(current_step)
            continue

        bullet = BULLET_RE.match(text)
        if bullet and current_step is not None:
            item = bullet.group(1).strip()
            current_step["subitems"].append(
                {
                    "text": item,
                    "source": _line_ref(line),
                }
            )
            continue

        # Continuation text is attached only after a numbered step exists.
        # Otherwise it is preserved but never silently promoted to semantics.
        if current_step is not None:
            current_step["text"] = (current_step["text"] + " " + text).strip()
            current_step["source_lines"].append(_line_ref(line))
        else:
            uncategorised_lines.append(_line_ref(line))

    if current_phase is not None:
        _finalise_phase(current_phase)

    if len(all_steps) < 2 or not phases:
        # A repair number alone is insufficient evidence for a procedure.
        return None

    review_reasons: list[str] = []
    if title is None:
        review_reasons.append("missing_title")
    if any(step["phase"] is None for step in all_steps):
        review_reasons.append("step_without_phase")

    phase_sequence_statuses = [
        {
            "phase": phase["label"],
            "source_label": phase["source_label"],
            "step_numbers": phase["step_numbers"],
            "status": phase["step_sequence_status"],
        }
        for phase in phases
    ]
    bad_phase_sequences = [
        phase_status
        for phase_status in phase_sequence_statuses
        if phase_status["status"] not in {"contiguous", "single_step", "none"}
    ]
    if bad_phase_sequences:
        review_reasons.append("phase_step_sequence_not_contiguous")

    low_confidence = []
    for step in all_steps:
        for source in step["source_lines"]:
            confidence = source.get("mean_confidence")
            if confidence is not None and float(confidence) < 70:
                low_confidence.append(source)
    if low_confidence:
        review_reasons.append("low_ocr_confidence_in_step")

    numbers = [step["number"] for step in all_steps]
    if bad_phase_sequences:
        overall_sequence = "phase_sequence_issue"
    elif any(step["phase"] is None for step in all_steps):
        overall_sequence = "unphased_steps_present"
    else:
        overall_sequence = "contiguous_by_phase"

    return {
        "structure_version": STRUCTURE_VERSION,
        "kind": "repair_procedure_candidate",
        "status": "REVIEW_REQUIRED",
        "page_number": page_number,
        "region": region,
        "title": title,
        "title_source": title_source,
        "repair_number": repair_match.group(1),
        "repair_number_source": _line_ref(lines[repair_index]),
        "phases": phases,
        "steps": all_steps,
        "step_numbers": numbers,
        "step_sequence_status": overall_sequence,
        "phase_sequence_statuses": phase_sequence_statuses,
        "uncategorised_lines": uncategorised_lines,
        "review_reasons": review_reasons,
    }


def _numbered_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for line in lines:
        text = _normalise_space(str(line.get("text", "")))
        match = STEP_RE.match(text)
        if match:
            result.append(
                {
                    "number": int(match.group(1)),
                    "text": match.group(2).strip(),
                    "source": _line_ref(line),
                }
            )
    return result


def _has_standalone_heading(lines: list[dict[str, Any]], heading: str) -> bool:
    """Require a heading-like line, never a substring inside ordinary prose."""
    wanted = _normalise_space(heading).upper()
    for line in lines:
        text = _normalise_space(str(line.get("text", ""))).strip(" .:-").upper()
        if text == wanted:
            return True
    return False


def analyze_structure_candidates(layout_payload: dict[str, Any]) -> dict[str, Any]:
    page_number = int(layout_payload["page_number"])
    all_lines = list(layout_payload.get("lines", []))
    by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in all_lines:
        by_region[str(line.get("region") or "unknown")].append(line)

    procedures: list[dict[str, Any]] = []
    for region, lines in by_region.items():
        if region in {"header", "footer", "spanning"}:
            continue
        repair_indices = [
            index
            for index, line in enumerate(lines)
            if REPAIR_NO_RE.search(_normalise_space(str(line.get("text", ""))))
        ]
        for ordinal, repair_index in enumerate(repair_indices):
            title_index = _nearest_title_index(lines, repair_index)
            next_repair_index = repair_indices[ordinal + 1] if ordinal + 1 < len(repair_indices) else len(lines)
            if ordinal + 1 < len(repair_indices):
                next_title = _nearest_title_index(lines, next_repair_index)
                end_index = next_title if next_title is not None and next_title > repair_index else next_repair_index
            else:
                end_index = len(lines)
            candidate = _parse_procedure_segment(
                page_number=page_number,
                region=region,
                lines=lines,
                title_index=title_index,
                repair_index=repair_index,
                end_index=end_index,
            )
            if candidate is not None:
                procedures.append(candidate)

    body_regions = [region for region in by_region if region not in {"header", "footer", "spanning"}]
    numbered = []
    for region in body_regions:
        numbered.extend(_numbered_lines(by_region[region]))

    if procedures:
        page_class = "repair_procedure_page"
    elif _has_standalone_heading(all_lines, "CONTENTS") and len(all_lines) >= 8:
        page_class = "contents_page_candidate"
    elif len(numbered) >= 5:
        page_class = "numbered_component_or_reference_list_candidate"
    elif _has_standalone_heading(all_lines, "INTRODUCTION"):
        page_class = "front_matter_or_narrative_candidate"
    else:
        page_class = "unclassified_candidate"

    return {
        "structure_version": STRUCTURE_VERSION,
        "page_number": page_number,
        "status": "CANDIDATE_ONLY",
        "page_class": page_class,
        "procedures": procedures,
        "procedure_count": len(procedures),
        "numbered_line_count": len(numbered),
        "numbered_lines": numbered,
        "warning": (
            "Ces structures sont des candidats issus de la géométrie OCR. "
            "Aucune procédure, liste ou classe de page n'est publiée comme donnée technique avant validation."
        ),
    }


def run_structure_prototype(layout_root: pathlib.Path, output_root: pathlib.Path) -> dict[str, Any]:
    layout_root = pathlib.Path(layout_root)
    output_root = pathlib.Path(output_root)
    page_files = sorted(layout_root.glob("*/pages/page-*.json"))
    if not page_files:
        raise RuntimeError(f"Aucune page de layout trouvée dans {layout_root}")

    results: list[dict[str, Any]] = []
    for page_file in page_files:
        layout = json.loads(page_file.read_text(encoding="utf-8"))
        structure = analyze_structure_candidates(layout)
        pdf_dir_name = page_file.parents[1].name
        out_dir = output_root / pdf_dir_name / "pages"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / page_file.name).write_text(
            json.dumps(structure, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        results.append(
            {
                "page_number": structure["page_number"],
                "page_class": structure["page_class"],
                "procedure_count": structure["procedure_count"],
                "numbered_line_count": structure["numbered_line_count"],
                "repair_numbers": [p["repair_number"] for p in structure["procedures"]],
                "procedure_titles": [p["title"] for p in structure["procedures"]],
                "procedure_sequence_statuses": [p["step_sequence_status"] for p in structure["procedures"]],
            }
        )

    summary = {
        "structure_version": STRUCTURE_VERSION,
        "sampled_page_count": len(results),
        "procedure_candidate_count": sum(r["procedure_count"] for r in results),
        "pages": results,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary
