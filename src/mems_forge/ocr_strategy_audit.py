"""Automatic OCR strategy diagnostics grounded in the original PDF image.

A page-wide OCR score cannot guarantee document-reading order, list numbering,
or technical values. Compare independent Tesseract segmentation modes and
split real two-column pages before constructing technical knowledge.

This is deliberately a diagnostic, NOT a release or knowledge approval gate.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

import pymupdf

from .ocr_layout import _best_gutter
from .ocr_reader import _parse_words

_LIST_ITEM = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+\w", re.MULTILINE)


def _numbers(text: str) -> dict:
    numbers = [int(n) for n in _LIST_ITEM.findall(text)]
    seen: set[int] = set()
    duplicates: list[int] = []
    for number in numbers:
        if number in seen and number not in duplicates:
            duplicates.append(number)
        seen.add(number)
    gaps: list[int] = []
    if numbers and len(numbers) > 1:
        gaps = [n for n in range(min(numbers), max(numbers) + 1) if n not in seen]
    return {
        "numbers": numbers,
        "count": len(numbers),
        "repeats": duplicates,
        "gaps": gaps,
        "starts_at_one": bool(numbers and numbers[0] == 1),
    }


def _ocr(png: Path, language: str, segmentation: int, *, tsv: bool = False) -> str:
    if segmentation not in (3, 4):
        raise ValueError("UNSUPPORTED_OCR_STRATEGY")
    result = subprocess.run(
        ["tesseract", str(png), "stdout", "-l", language,
         "--psm", str(segmentation), "tsv" if tsv else "txt"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout


def analyze_raster_reading(
    pdf_path: Path, page_number: int, *, language: str = "eng", dpi: int = 300,
) -> dict:
    """Run competing OCR layouts on ONE chosen PDF page, without modifications.

    A two-column alternative is generated in separate left/right ROIs. OCR
    disagreements are surfaced; no 'best-looking' text is substituted silently.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file() or not (150 <= dpi <= 400):
        raise ValueError("INVALID_OR_MISSING_PDF")
    if page_number < 1:
        raise ValueError("INVALID_PAGE_NUMBER")
    with pymupdf.open(pdf_path) as document:
        if page_number > document.page_count:
            raise ValueError("PAGE_OUT_OF_RANGE")
        page = document.load_page(page_number - 1)
        with tempfile.TemporaryDirectory(prefix="mems-forge-ocr-strategies-") as temp:
            directory = Path(temp)
            scale = dpi / 72.0
            bitmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                colorspace=pymupdf.csGRAY, alpha=False,
            )
            original_png = directory / "full.png"
            bitmap.save(original_png)
            primary = _ocr(original_png, language, 3)
            alternative = _ocr(original_png, language, 4)
            primary_tsv = _ocr(original_png, language, 3, tsv=True)
            words, warnings = _parse_words(primary_tsv, page_number)
            body = [
                asdict(word) for word in words
                if (word.top >= bitmap.height * .055 and
                    word.top < bitmap.height * .94)
            ]
            gutter = _best_gutter(body, bitmap.width)
            two_columns = bool(gutter and gutter.get("plausible_two_columns"))
            regions = {}
            if two_columns:
                split_pt = gutter["x"] / scale
                for side, rect in (
                    ("left", pymupdf.Rect(0, 0, split_pt, page.rect.height)),
                    ("right", pymupdf.Rect(split_pt, 0, page.rect.width, page.rect.height)),
                ):
                    if rect.is_empty or rect.width < page.rect.width * .20:
                        raise ValueError("INVALID_COLUMN_GEOMETRY")
                    pix = page.get_pixmap(
                        matrix=pymupdf.Matrix(scale, scale), clip=rect,
                        colorspace=pymupdf.csGRAY, alpha=False,
                    )
                    png = directory / (side + ".png")
                    pix.save(png)
                    text = _ocr(png, language, 4)
                    regions[side] = {
                        "text": text,
                        "numbering": _numbers(text),
                        "bbox_pdf": [rect.x0, rect.y0, rect.x1, rect.y1],
                    }
            numbered = {
                "psm3_whole": _numbers(primary),
                "psm4_whole": _numbers(alternative),
            }
            defects = []
            for label, report in numbered.items():
                if report["repeats"] or report["gaps"]:
                    defects.append({"code": "LIST_SEQUENCE_DISCONTINUITY",
                                    "strategy": label,
                                    "repeats": report["repeats"],
                                    "gaps": report["gaps"]})
            for side, entry in regions.items():
                if entry["numbering"]["repeats"] or entry["numbering"]["gaps"]:
                    defects.append({
                        "code": "COLUMN_LIST_SEQUENCE_DISCONTINUITY",
                        "strategy": "psm4_" + side,
                        "repeats": entry["numbering"]["repeats"],
                        "gaps": entry["numbering"]["gaps"],
                    })
            if two_columns:
                defects.append({"code": "FULL_PAGE_READING_ORDER_UNTRUSTWORTHY",
                                "strategy": "psm3_whole"})
            if primary != alternative:
                defects.append({"code": "OCR_MODES_DISAGREE",
                                "strategy": "psm3_vs_psm4"})
            return {
                "format": "mems-forge-ocr-strategy-audit",
                "version": 1,
                "original_pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                "pdf_page": page_number,
                "dpi": dpi,
                "image_sha256": hashlib.sha256(original_png.read_bytes()).hexdigest(),
                "layout": "two_columns" if two_columns else "single_or_complex",
                "gutter_x_pixel": gutter["x"] if two_columns else None,
                "psm3_text": primary,
                "psm4_text": alternative,
                "numbering": numbered,
                "regions": regions,
                "parse_warnings": warnings,
                "defects": defects,
                "approved_for_technical_database": False,
                "requires_user_manual_validation": False,
            }


def write_raster_strategy_audit(
    pdf_path: Path, page_number: int, destination: Path,
    *, language: str = "eng", dpi: int = 300,
) -> dict:
    report = analyze_raster_reading(
        pdf_path, page_number, language=language, dpi=dpi,
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report
