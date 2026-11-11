"""Coverage accounting for adaptive OCR on every page of one source PDF.

Coverage is NOT recognition accuracy. Technical publication stays blocked until
facts, source evidence, applicability and image/table meaning are validated.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


def audit_adaptive_coverage(ocr_root: Path, source_sha256: str, page_count: int) -> dict:
    if page_count < 1 or len(source_sha256) != 64:
        raise ValueError("INVALID_SOURCE_IDENTITY")
    root = Path(ocr_root) / source_sha256
    pages = root / "pages"
    raw = root / "raw"
    expected = [f"page-{n:04d}.json" for n in range(1, page_count + 1)]
    actual = sorted(p.name for p in pages.glob("page-*.json"))
    if actual != expected:
        raise ValueError("ADAPTIVE_PAGE_COVERAGE_INCOMPLETE")
    status_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    page_details: list[dict] = []
    for n, filename in enumerate(expected, 1):
        item = json.loads((pages / filename).read_text(encoding="utf-8"))
        if (item.get("source_sha256") != source_sha256 or
                item.get("page_number") != n or
                item.get("adaptive_evaluation_version") != 1):
            raise ValueError(f"ADAPTIVE_PAGE_NOT_EVALUATED:{n}")
        if not (raw / f"page-{n:04d}.tsv.gz").is_file():
            raise ValueError(f"OCR_EVIDENCE_MISSING:{n}")
        variants = item.get("adaptive_extraction") or {}
        recovered = variants.get("recovered_list")
        columns = variants.get("columns") or {}
        failures = []
        if item.get("adaptive_failure"):
            failures.append("REGION_RECHECK_FAILED")
        if item.get("parse_warnings"):
            failures.append("OCR_TSV_PARSE_WARNINGS")
        visual = item.get("page_content") or {}
        is_confident_blank = (
            visual.get("kind") == "BLANK_SCAN"
            and visual.get("ocr_skipped") is True
        )
        if not item.get("words") and not is_confident_blank:
            failures.append(
                "SPARSE_PAGE_NO_OCR_WORDS"
                if visual.get("kind") == "SPARSE_OR_NOISE"
                else "VISUAL_ONLY_OR_UNRECOGNIZED_CONTENT"
            )
        if is_confident_blank and item.get("words"):
            failures.append("BLANK_PAGE_HAS_OCR_WORDS")
        if item.get("adaptive_empty_regions"):
            failures.append("ADAPTIVE_REGIONS_WITHOUT_OCR_TEXT")
        if recovered and not recovered.get("independent_render_readings_agree"):
            failures.append("COLLAPSED_LIST_NOT_RECONCILED")
        for column in ("left", "right"):
            entry = columns.get(column)
            if not entry:
                continue
            numbering = entry.get("numbering") or {}
            if numbering.get("gaps") or numbering.get("repeats"):
                failures.append(f"COLUMN_{column.upper()}_STEP_NUMBER_CONFLICT")
        for failure in failures:
            issue_counts[failure] += 1
        # All pages have had the same *evaluation*, but only a subset need
        # expensive regional reruns. No confidence score certifies correctness.
        state = "UNRESOLVED" if failures else (
            "BLANK_SCAN" if is_confident_blank
            else "RECHECKED_OCR_CANDIDATE" if recovered or columns
            else "BASELINE_OCR_CHECKED"
        )
        status_counts[state] += 1
        page_details.append({
            "page": n,
            "state": state,
            "ocr_words": len(item.get("words") or []),
            "visual_content_kind": visual.get("kind", "UNKNOWN"),
            "skipped_confirmed_blank": is_confident_blank,
            "columns": sorted(columns),
            "vertical_list_rerun": recovered is not None,
            "vertical_list_crosschecked": bool(
                recovered and recovered.get("independent_render_readings_agree")
            ),
            "issues": failures,
        })
    return {
        "format": "mems-forge-adaptive-coverage",
        "version": 1,
        "source_sha256": source_sha256,
        "source_pages": page_count,
        "pages_evaluated": len(page_details),
        "every_page_evaluated": len(page_details) == page_count,
        "status_counts": dict(sorted(status_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "unresolved_pages": [p["page"] for p in page_details if p["issues"]],
        "pages": page_details,
        "ocr_accuracy_certified": False,
        "structured_technical_facts_validated": False,
        "approved_for_automatic_ai_answers": False,
        "approved_for_publication": False,
    }


def write_adaptive_coverage(
    ocr_root: Path, source_sha256: str, page_count: int, destination: Path,
) -> dict:
    report = audit_adaptive_coverage(ocr_root, source_sha256, page_count)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report
