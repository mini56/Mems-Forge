"""Every PDF page must receive OCR evaluation; no accidental approval."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mems_forge.adaptive_coverage import (
    audit_adaptive_coverage, write_adaptive_coverage,
)


def _page(root: Path, sha: str, n: int, *,
          words: bool = True, recovered=None, columns=None,
          failure=None, parse_warnings=None):
    target = root / sha
    (target / "pages").mkdir(parents=True, exist_ok=True)
    (target / "raw").mkdir(parents=True, exist_ok=True)
    (target / "raw" / f"page-{n:04d}.tsv.gz").write_bytes(b"source")
    (target / "pages" / f"page-{n:04d}.json").write_text(
        json.dumps({
            "source_sha256": sha,
            "page_number": n,
            "adaptive_evaluation_version": 1,
            "words": [{"text": "ECU"}] if words else [],
            "adaptive_extraction": {
                "recovered_list": recovered,
                "columns": columns or {},
            } if recovered or columns else None,
            "adaptive_failure": failure,
            "parse_warnings": parse_warnings or [],
        }),
        encoding="utf-8",
    )


def test_every_page_is_evaluated_and_unresolved_is_separate(tmp_path: Path):
    sha = "a" * 64
    _page(tmp_path, sha, 1)
    _page(tmp_path, sha, 2, recovered={
        "independent_render_readings_agree": True,
    })
    _page(tmp_path, sha, 3, words=False)
    report = write_adaptive_coverage(tmp_path, sha, 3,
                                     tmp_path / "report.json")
    assert report["pages_evaluated"] == 3
    assert report["every_page_evaluated"] is True
    assert report["unresolved_pages"] == [3]
    assert report["status_counts"] == {
        "BASELINE_OCR_CHECKED": 1,
        "RECHECKED_OCR_CANDIDATE": 1,
        "UNRESOLVED": 1,
    }
    assert report["approved_for_publication"] is False
    assert report["ocr_accuracy_certified"] is False
    assert json.loads((tmp_path / "report.json").read_text()) == report


def test_missing_page_is_a_hard_failure(tmp_path: Path):
    sha = "b" * 64
    _page(tmp_path, sha, 1)
    _page(tmp_path, sha, 3)
    with pytest.raises(ValueError, match="ADAPTIVE_PAGE_COVERAGE_INCOMPLETE"):
        audit_adaptive_coverage(tmp_path, sha, 3)


def test_unchecked_page_is_a_hard_failure(tmp_path: Path):
    sha = "c" * 64
    _page(tmp_path, sha, 1)
    path = tmp_path / sha / "pages" / "page-0001.json"
    entry = json.loads(path.read_text())
    del entry["adaptive_evaluation_version"]
    path.write_text(json.dumps(entry))
    with pytest.raises(ValueError, match="ADAPTIVE_PAGE_NOT_EVALUATED"):
        audit_adaptive_coverage(tmp_path, sha, 1)


def test_conflicting_column_and_list_is_not_silently_approved(tmp_path: Path):
    sha = "d" * 64
    _page(tmp_path, sha, 1,
          recovered={"independent_render_readings_agree": False},
          columns={"left": {
              "numbering": {"gaps": [9], "repeats": [8]},
          }},
          failure="Tesseract failed")
    result = audit_adaptive_coverage(tmp_path, sha, 1)
    assert result["unresolved_pages"] == [1]
    assert set(result["pages"][0]["issues"]) == {
        "REGION_RECHECK_FAILED", "COLLAPSED_LIST_NOT_RECONCILED",
        "COLUMN_LEFT_STEP_NUMBER_CONFLICT",
    }
    assert not result["approved_for_automatic_ai_answers"]
