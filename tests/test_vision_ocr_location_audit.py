"""OCR against original PDF geometry must never auto-fix model proposals."""
from __future__ import annotations

import pytest

from mems_forge.vision_ocr_location_audit import verify_ocr_quote_locations


def _tsv() -> str:
    return (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
        "5\t116\t1\t1\t1\t1\t140\t100\t20\t10\t91\t1.\n"
        "5\t116\t1\t1\t1\t2\t162\t100\t65\t10\t93\tOxygen\n"
        "5\t116\t1\t1\t1\t3\t231\t100\t57\t10\t94\tsensor\n"
        "5\t116\t1\t1\t2\t1\t140\t150\t20\t10\t90\t2.\n"
        "5\t116\t1\t1\t2\t2\t162\t150\t38\t10\t94\tFuel\n"
        "5\t116\t1\t1\t2\t3\t202\t150\t50\t10\t94\tpump\n"
    )


def _report() -> dict:
    return {
        "physical_page": 116,
        "source_pdf_sha256": "a"*64,
        "approved_for_publication": False,
        "items": [
            {"source_quote": "1. Oxygen sensor",
             "bbox_1000": [140, 30, 400, 50]},
            {"source_quote": "2. Fuel pump",
             "bbox_1000": [140, 150, 254, 161]},
            {"source_quote": "3. Invented relay",
             "bbox_1000": [140, 150, 254, 161]},
        ],
    }


def test_wrong_geometry_and_missing_quote_are_not_approved():
    r = verify_ocr_quote_locations(
        _report(), _tsv(), render_width=1000, render_height=1000,
    )
    assert r["ocr_words"] == 6
    assert [i["status"] for i in r["items"]] == [
        "MODEL_LOCATION_CONFLICTS_WITH_OCR_CANDIDATE",
        "MODEL_LOCATION_AGREES_WITH_OCR_CANDIDATE",
        "QUOTE_NOT_FOUND_IN_INDEPENDENT_OCR",
    ]
    assert r["location_conflicts"] == 1
    assert r["ocr_unmatched_quotes"] == 1
    assert r["items"][0]["source_ocr_bbox_1000"] == [140, 100, 288, 110]
    assert r["approved_for_publication"] is False
    assert all(item["approved_for_ai"] is False for item in r["items"])


def test_approved_report_is_not_a_valid_candidate():
    r = _report()
    r["approved_for_publication"] = True
    with pytest.raises(ValueError, match="ONLY_UNAPPROVED"):
        verify_ocr_quote_locations(
            r, _tsv(), render_width=1000, render_height=1000,
        )


def test_bad_ocr_is_not_used_as_proof():
    with pytest.raises(ValueError, match="OCR_SOURCE_TSV_UNTRUSTED"):
        verify_ocr_quote_locations(
            _report(), "broken-data", render_width=1000, render_height=1000,
        )
