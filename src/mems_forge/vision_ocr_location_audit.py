"""Confront model-proposed locations with OCR evidence from the SAME PDF page.

Matching words are evidence of transcription, not proof that the words, model
meaning or applicability are correct. A wrong position remains an unresolved
finding. Never move a model bbox silently or approve a technical fact.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .ocr_reader import _parse_words

_WORD = re.compile(r"[a-z0-9]+", flags=re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return [v.casefold() for v in _WORD.findall(text)]


def _bbox_of_words(words: list[Any], width: int, height: int) -> list[int]:
    x0 = min(word.left for word in words)
    y0 = min(word.top for word in words)
    x1 = max(word.left + word.width for word in words)
    y1 = max(word.top + word.height for word in words)
    return [
        max(0, min(1000, round(x0 * 1000 / width))),
        max(0, min(1000, round(y0 * 1000 / height))),
        max(0, min(1000, round(x1 * 1000 / width))),
        max(0, min(1000, round(y1 * 1000 / height))),
    ]


def _iou(a: list[int], b: list[int]) -> float:
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    if not overlap:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return round(overlap / (area_a + area_b - overlap), 4)


def verify_ocr_quote_locations(
    vision_report: dict, ocr_tsv: str, *,
    render_width: int, render_height: int,
) -> dict:
    if render_width < 1 or render_height < 1:
        raise ValueError("INVALID_OCR_IMAGE_GEOMETRY")
    physical_page = int(vision_report["physical_page"])
    entries = vision_report["items"]
    if (not isinstance(entries, list) or
            vision_report.get("approved_for_publication") is not False):
        raise ValueError("ONLY_UNAPPROVED_VISION_REPORTS_MAY_BE_CHECKED")
    raw_words, warnings = _parse_words(ocr_tsv, physical_page)
    if warnings:
        raise ValueError("OCR_SOURCE_TSV_UNTRUSTED")
    # Preserve the actual reading sequence from the TSV; punctuation tokens
    # such as a dash may split a source quote into multiple OCR words.
    token_words = []
    for word in raw_words:
        for token in _tokens(word.text):
            token_words.append((token, word))
    source_tokens = [pair[0] for pair in token_words]
    checks = []
    for index, item in enumerate(entries):
        quote_tokens = _tokens(str(item["source_quote"]))
        matches = []
        if quote_tokens:
            for offset in range(len(source_tokens) - len(quote_tokens) + 1):
                if source_tokens[offset:offset + len(quote_tokens)] == quote_tokens:
                    excerpt = [word for _, word in
                               token_words[offset:offset + len(quote_tokens)]]
                    matches.append(_bbox_of_words(excerpt, render_width, render_height))
        if not matches:
            status = "QUOTE_NOT_FOUND_IN_INDEPENDENT_OCR"
            bbox = None
            overlap = None
        elif len(matches) > 1:
            status = "QUOTE_NOT_UNIQUE_IN_INDEPENDENT_OCR"
            bbox = None
            overlap = None
        else:
            bbox = matches[0]
            overlap = _iou(item["bbox_1000"], bbox)
            status = (
                "MODEL_LOCATION_AGREES_WITH_OCR_CANDIDATE" if overlap >= 0.3
                else "MODEL_LOCATION_CONFLICTS_WITH_OCR_CANDIDATE"
            )
        checks.append({
            "item_index": index,
            "source_quote": item["source_quote"],
            "model_bbox_1000": item["bbox_1000"],
            "source_ocr_bbox_1000": bbox,
            "bbox_iou": overlap,
            "source_quote_matches": len(matches),
            "status": status,
            "semantic_technical_validation": False,
            "approved_for_ai": False,
        })
    return {
        "format": "mems-forge-vision-ocr-location-audit",
        "version": 1,
        "source_pdf_sha256": vision_report["source_pdf_sha256"],
        "physical_page": physical_page,
        "source_ocr_tsv_sha256": hashlib.sha256(ocr_tsv.encode("utf-8")).hexdigest(),
        "ocr_words": len(raw_words),
        "items": checks,
        "location_conflicts": sum(
            c["status"] == "MODEL_LOCATION_CONFLICTS_WITH_OCR_CANDIDATE"
            for c in checks
        ),
        "ocr_unmatched_quotes": sum(
            c["status"].startswith("QUOTE_NOT_") for c in checks
        ),
        "approved_for_publication": False,
    }


def write_location_audit(report: dict, tsv: str, destination: Path,
                         *, render_width: int, render_height: int) -> dict:
    audit = verify_ocr_quote_locations(
        report, tsv, render_width=render_width, render_height=render_height,
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return audit
