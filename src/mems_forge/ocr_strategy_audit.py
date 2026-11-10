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
    if segmentation not in (3, 4, 6):
        raise ValueError("UNSUPPORTED_OCR_STRATEGY")
    result = subprocess.run(
        ["tesseract", str(png), "stdout", "-l", language,
         "--psm", str(segmentation), "tsv" if tsv else "txt"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout



def _recover_vertical_list_roi(words, width: int, height: int) -> list[int] | None:
    """Detect collapsed vertical OCR rows by geometry, never by expected values.

    Example: on a real scanned parts list, PSM3 merged labels 1..10 into one
    extremely tall token. Its box gives a region to re-read independently.
    A common printed header/footer is excluded by its geometry.
    """
    import statistics

    heights = [int(w.height) for w in words if 4 <= w.height < height * 0.1]
    if not heights:
        return None
    typical = statistics.median(heights)
    tall = [
        w for w in words
        if (w.height >= max(80, typical * 5)
            and w.width < width * 0.18
            and w.left < width * 0.55
            and height * 0.04 < w.top < height * 0.82)
    ]
    if not tall:
        return None
    trigger = max(tall, key=lambda w: w.height / max(1, w.width))
    # Extend horizontally to the words belonging to the list, and below the
    # collapsed rows to include later numbered entries.
    x0 = max(0, trigger.left - 40)
    x1 = min(width, max(trigger.left + width * 0.45, x0 + width * 0.32))
    y0 = max(0, trigger.top - max(40, typical * 2))
    y1 = min(height * 0.92,
             trigger.top + trigger.height + max(550, height * 0.16))
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(x0), round(y0), round(x1), round(y1)]


def _numbered_lines(text: str) -> dict[int, str]:
    items = {}
    for line in text.splitlines():
        hit = _LIST_ITEM.match(line)
        if hit:
            label = int(hit.group(1))
            if label in items:
                return {}
            items[label] = line.strip()
    return items


def _recover_list_with_two_resolutions(
    page, primary_words, image_width: int, image_height: int,
    language: str, directory: Path, primary_dpi: int,
) -> dict | None:
    """Read collapsed list region twice, from the ORIGINAL PDF at 200/400 DPI.

    Only an identical complete sequence AND identical line descriptions is
    admitted as a cross-checked OCR alternative. This still does not imply
    semantic or technical approval.
    """
    roi = _recover_vertical_list_roi(primary_words, image_width, image_height)
    if not roi:
        return None
    original_scale = primary_dpi / 72.0
    pdf_rect = pymupdf.Rect(*[coordinate / original_scale for coordinate in roi])
    if pdf_rect.is_empty:
        return None
    variants = {}
    for dpi in (200, 400):
        scale = dpi / 72.0
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), clip=pdf_rect,
            colorspace=pymupdf.csGRAY, alpha=False,
        )
        filename = directory / f"collapsed-list-{dpi}.png"
        pix.save(filename)
        content = _ocr(filename, language, 6)
        variants[str(dpi)] = {
            "text": content, "numbering": _numbers(content),
            "lines": _numbered_lines(content),
        }
    smaller, larger = variants["200"], variants["400"]
    first = smaller["numbering"]
    second = larger["numbering"]
    consistent = (
        first["starts_at_one"] and second["starts_at_one"]
        and len(first["numbers"]) >= 5
        and first["numbers"] == second["numbers"]
        and not first["repeats"] and not first["gaps"]
        and not second["repeats"] and not second["gaps"]
        and smaller["lines"] == larger["lines"]
        and len(smaller["lines"]) == first["count"]
    )
    return {
        "roi_pdf": [pdf_rect.x0, pdf_rect.y0, pdf_rect.x1, pdf_rect.y1],
        "roi_source": "vertical_collapsed_ocr_geometry",
        "resolution_dpi": [200, 400],
        "numbering": first,
        "candidate_text": larger["text"],
        "independent_render_readings_agree": bool(consistent),
        "validation_state": (
            "CROSSCHECKED_OCR_ONLY_NOT_TECHNICAL_VALIDATION" if consistent
            else "OCR_READINGS_CONFLICT_NOT_APPROVED"
        ),
        "approved_for_technical_database": False,
    }


def _recheck_two_column_regions(
    page, words, width: int, height: int, language: str,
    directory: Path, dpi: int,
) -> dict | None:
    """Independently OCR left/right image regions to avoid interleaved steps.

    All coordinates are anchored to the real PDF and the original PSM3 words.
    A high list score selects an *OCR candidate*, not a technical fact.
    """
    body = [
        asdict(word) for word in words
        if (word.top >= height * .055 and word.top < height * .94)
    ]
    gutter = _best_gutter(body, width)
    if not (gutter and gutter.get("plausible_two_columns")):
        return None
    split_pt = gutter["x"] / (dpi / 72.0)
    regions = {}
    for side, rect in (
        ("left", pymupdf.Rect(0, 0, split_pt, page.rect.height)),
        ("right", pymupdf.Rect(split_pt, 0, page.rect.width, page.rect.height)),
    ):
        if rect.is_empty or rect.width < page.rect.width * .20:
            raise ValueError("INVALID_COLUMN_GEOMETRY")
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0), clip=rect,
            colorspace=pymupdf.csGRAY, alpha=False,
        )
        png = directory / (side + ".png")
        pix.save(png)
        versions = {}
        for psm in (4, 6):
            candidate = _ocr(png, language, psm)
            versions[f"psm{psm}"] = {
                "text": candidate, "numbering": _numbers(candidate),
            }

        def quality(item):
            n = item["numbering"]
            return (n["count"] - len(n["repeats"]) * 3 -
                    len(n["gaps"]) * 2 + int(n["starts_at_one"]))

        preferred = ("psm6" if quality(versions["psm6"]) >
                     quality(versions["psm4"]) else "psm4")
        regions[side] = {
            "candidate_mode": preferred,
            "text": versions[preferred]["text"],
            "numbering": versions[preferred]["numbering"],
            "versions": versions,
            "bbox_pdf": [rect.x0, rect.y0, rect.x1, rect.y1],
            "approved_for_technical_database": False,
        }
    return {"gutter": gutter, "regions": regions}


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
            two_column = _recheck_two_column_regions(
                page, words, bitmap.width, bitmap.height,
                language, directory, dpi,
            )
            gutter = two_column["gutter"] if two_column else None
            two_columns = two_column is not None
            regions = two_column["regions"] if two_column else {}
            numbered = {
                "psm3_whole": _numbers(primary),
                "psm4_whole": _numbers(alternative),
            }
            recovered_list = _recover_list_with_two_resolutions(
                page, words, bitmap.width, bitmap.height,
                language, directory, dpi,
            )
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
            for side, entry in regions.items():
                if entry["versions"]["psm4"]["text"] != entry["versions"]["psm6"]["text"]:
                    defects.append({
                        "code": "COLUMN_OCR_MODES_DISAGREE",
                        "strategy": side,
                        "candidate_mode": entry["candidate_mode"],
                    })
            if two_columns:
                defects.append({"code": "FULL_PAGE_READING_ORDER_UNTRUSTWORTHY",
                                "strategy": "psm3_whole"})
            if primary != alternative:
                defects.append({"code": "OCR_MODES_DISAGREE",
                                "strategy": "psm3_vs_psm4"})
            if recovered_list and not recovered_list["independent_render_readings_agree"]:
                defects.append({"code": "COLLAPSED_LIST_REOCR_CONFLICT",
                                "strategy": "original_pdf_multi_dpi"})
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
                "recovered_list": recovered_list,
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
