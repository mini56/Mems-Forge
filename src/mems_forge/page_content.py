"""Conservative content preflight on original PDF pixels, before OCR.

Never infer that a PDF is blank because OCR returns no words. A scanned white
page containing dust is different from a full-page diagram that OCR cannot read.
Original page images are always preserved in the source PDF.
"""
from __future__ import annotations

from dataclasses import dataclass
import pymupdf


_PREFLIGHT_DPI = 72
# At 72 DPI, 0.005% dark pixels means only a few isolated scanner specks.
# 0.1% is NEAR_BLANK rather than certified blank; it still requires OCR.
_CONFIDENT_BLANK_MAX_DARK = 0.00005
_SPARSE_MAX_DARK = 0.001


def classify_page(page: pymupdf.Page) -> dict:
    """Examine raster density and PDF content WITHOUT invoking OCR.

    This gate is intentionally asymmetric: false 'blank' loses information,
    whereas unnecessary OCR merely costs time. Borderline pages remain
    searchable candidates, never silently discarded.
    """
    if page.get_text("text").strip():
        return {
            "kind": "NATIVE_TEXT",
            "ocr_skipped": False,
            "decision": "native_text_present",
            "nonwhite_ratio": None,
            "preflight_dpi": _PREFLIGHT_DPI,
        }
    bitmap = page.get_pixmap(
        matrix=pymupdf.Matrix(1, 1),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    pixels = memoryview(bitmap.samples)
    if not pixels:
        raise ValueError("EMPTY_PDF_RENDER")
    dark_count = sum(pixel < 220 for pixel in pixels)
    ratio = dark_count / len(pixels)

    if ratio <= _CONFIDENT_BLANK_MAX_DARK:
        # Drawings and annotation marks can be sparse but meaningful even when
        # a text extractor returns nothing.
        has_drawings = bool(page.get_drawings())
        has_annotations = page.first_annot is not None
        if not has_drawings and not has_annotations:
            return {
                "kind": "BLANK_SCAN",
                "ocr_skipped": True,
                "decision": "original_image_nearly_white_without_vectors",
                "nonwhite_ratio": ratio,
                "preflight_dpi": _PREFLIGHT_DPI,
            }
    if ratio <= _SPARSE_MAX_DARK:
        kind = "SPARSE_OR_NOISE"
        decision = "sparse_page_requires_ocr_and_source_preservation"
    else:
        kind = "RASTER_CONTENT"
        decision = "scan_requires_ocr"
    return {
        "kind": kind,
        "ocr_skipped": False,
        "decision": decision,
        "nonwhite_ratio": ratio,
        "preflight_dpi": _PREFLIGHT_DPI,
    }
