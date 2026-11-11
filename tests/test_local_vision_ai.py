"""Local vision AI contracts. Test failures must never approve technical facts."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from mems_forge.local_vision_ai import (
    _local_endpoint, check_vision_candidates, examine_pdf_page,
)


def _item(**changes):
    record = {
        "kind": "technical_value",
        "source_quote": "Coolant temperature 85 C",
        "bbox_1000": [115, 210, 450, 260],
        "value": "85", "unit": "C",
        "component": "Coolant temperature",
        "applicability": "",
    }
    record.update(changes)
    return record


def _pdf(tmp_path: Path, *, blank: bool = False) -> Path:
    target = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        if not blank:
            page.insert_text((70, 80), "Coolant temperature 85 C")
        doc.save(target)
    return target


def test_local_only_inference_endpoint():
    assert _local_endpoint("http://127.0.0.1:11434") == (
        "http://127.0.0.1:11434/api/chat"
    )
    assert _local_endpoint("http://localhost:11434/api/chat").endswith("/api/chat")
    for url in (
        "https://127.0.0.1:11434",
        "http://remote.example:11434",
        "http://127.0.0.1:11434/other",
        "http://127.0.0.1:11434/api/chat?upload=1",
    ):
        with pytest.raises(ValueError):
            _local_endpoint(url)


def test_ocr_corroboration_is_never_equated_to_truth():
    proposal = {"page_type": "table", "items": [_item()], "uncertainties": []}
    r = check_vision_candidates(proposal, ["Coolant temperature 85 C"])
    assert r["items"][0]["model_quote_in_ocr"] is True
    assert r["items"][0]["validation_state"] == "unresolved"
    assert r["items"][0]["approved_for_ai"] is False
    assert r["approved_for_publication"] is False


def test_uncorroborated_vision_remains_candidate_only():
    proposal = {"page_type": "mixed", "items": [_item()], "uncertainties": [
        "value too small to read",
    ]}
    r = check_vision_candidates(proposal, ["other text"])
    assert r["items"][0]["evidence_state"] == (
        "VISION_ONLY_REQUIRES_INDEPENDENT_CHECK"
    )
    assert not r["approved_for_automatic_ai_answers"]


def test_model_cannot_invent_page_bbox_or_blank_values():
    invalid = {"page_type": "technical_text", "items": [
        _item(bbox_1000=[-1, 0, 100, 20]),
    ], "uncertainties": []}
    with pytest.raises(ValueError, match="AI_PAGE_BBOX_INVALID"):
        check_vision_candidates(invalid, [])
    invalid["page_type"] = "blank"
    invalid["items"] = [_item()]
    with pytest.raises(ValueError, match="AI_BLANK_PAGE_CONTAINS_FACTS"):
        check_vision_candidates(invalid, [])


def test_confident_blank_original_is_skipped_without_model(tmp_path: Path):
    pdf = _pdf(tmp_path, blank=True)
    with patch("mems_forge.local_vision_ai._call_local_model") as model:
        report = examine_pdf_page(pdf, 1, model="qwen3-vl:4b")
    model.assert_not_called()
    assert report["page_type"] == "blank"
    assert report["local_model_invoked"] is False
    assert not report["approved_for_publication"]


def test_real_image_sent_to_local_model_and_is_only_candidate(tmp_path: Path):
    pdf = _pdf(tmp_path)
    proposal = {"page_type": "technical_text", "items": [_item()],
                "uncertainties": []}
    with patch("mems_forge.local_vision_ai._call_local_model",
               return_value=proposal) as model:
        report = examine_pdf_page(
            pdf, 1, model="qwen3-vl:4b",
            ocr_evidence=["Coolant temperature 85 C"],
        )
    assert model.call_count == 1
    args = model.call_args.args
    assert args[0] == "http://127.0.0.1:11434/api/chat"
    assert args[1] == "qwen3-vl:4b"
    assert args[2][:4] == bytes((137, 80, 78, 71))
    assert report["local_model_invoked"]
    assert report["items"][0]["model_quote_in_ocr"]
    assert report["items"][0]["approved_for_ai"] is False


def test_nonblank_image_cannot_be_declared_blank(tmp_path: Path):
    pdf = _pdf(tmp_path)
    with patch("mems_forge.local_vision_ai._call_local_model", return_value={
        "page_type": "blank", "items": [], "uncertainties": [],
    }):
        with pytest.raises(ValueError, match="AI_BLANK_CONTRADICTS_ORIGINAL_IMAGE"):
            examine_pdf_page(pdf, 1, model="qwen3-vl:4b")
