"""Coordinate quantization and source anchoring must be checked at the original PDF scale."""
from __future__ import annotations

from copy import deepcopy

import pytest

from mems_forge.document_pack import (
    _anchored_source_items, _bbox_within_original_ocr_region,
)


def _page304_source() -> dict:
    # These coordinates come from the actual 12-page GitHub Actions diagnostic
    # of physical Rover PDF page 304, with image OCR at 400 dpi.
    roi = [75.6, 500.64, 349.67999999999995, 693.8399999999999]
    item = {
        "number": 3,
        "source_text": "3. Fit bump rubber N",
        "bbox_pdf": [85.5, 540.78, 349.74, 553.02],
        "source": "original_pdf_tesseract_400dpi_tsv",
        "validation_state": "OCR_LOCATION_CANDIDATE",
        "approved_for_ai": False,
    }
    return {
        "pdf_page": {"width_points": 587.52, "height_points": 835.92},
        "adaptive_extraction": {
            "recovered_list": {
                "roi_pdf": roi,
                "candidate_text": (
                    "1. First instruction\n2. Second instruction\n"
                    "3. Fit bump rubber N\n"
                ),
                "numbering": {"numbers": [1, 2, 3]},
                "independent_render_readings_agree": True,
                "ocr_locations_crosschecked": True,
                "anchored_item_count": 3,
                "anchored_items": [
                    {
                        **item,
                        "number": 1, "source_text": "1. First instruction",
                        "bbox_pdf": [85.5, 510.0, 220.0, 520.0],
                    },
                    {
                        **item,
                        "number": 2, "source_text": "2. Second instruction",
                        "bbox_pdf": [85.5, 523.0, 220.0, 535.0],
                    },
                    item,
                ],
            }
        },
    }


def test_real_page304_coordinate_rounding_within_one_pixel_is_accepted():
    data = _page304_source()
    found = _anchored_source_items(data, 304)
    assert len(found) == 3
    assert found[2]["bbox_pdf"][2] == 349.74
    assert all(item["approved_for_ai"] is False for item in found)


def test_more_than_one_render_pixel_outside_source_remains_rejected():
    data = _page304_source()
    data["adaptive_extraction"]["recovered_list"]["anchored_items"][2][
        "bbox_pdf"
    ][2] = 350.0
    with pytest.raises(ValueError, match="Invalid OCR original source item 3"):
        _anchored_source_items(data, 304)


def test_source_text_tampering_still_fails():
    data = _page304_source()
    data["adaptive_extraction"]["recovered_list"]["anchored_items"][2][
        "source_text"
    ] = "3. Wrong source value"
    with pytest.raises(ValueError, match="Invalid OCR original source item 3"):
        _anchored_source_items(data, 304)


def test_boundary_tolerance_does_not_allow_false_entire_page_coordinates():
    data = _page304_source()
    roi = data["adaptive_extraction"]["recovered_list"]["roi_pdf"]
    assert _bbox_within_original_ocr_region(
        [85.5, 540.78, 349.74, 553.02], roi, 587.52, 835.92
    )
    assert not _bbox_within_original_ocr_region(
        [85.5, 540.78, 380.0, 553.02], roi, 587.52, 835.92
    )
