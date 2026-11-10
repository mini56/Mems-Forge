"""All changed PDF sources must be audited independently, without auto-publishing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from mems_forge import depot_pipeline as pipeline


def test_manual_selection_includes_every_pdf(tmp_path):
    depot = tmp_path / "depot"
    (depot / "a").mkdir(parents=True)
    (depot / "a" / "one.PDF").write_bytes(b"one")
    (depot / "two.pdf").write_bytes(b"two")
    (depot / "ignore.txt").write_text("not a pdf")
    assert len(pipeline.select_depot_pdfs(tmp_path, event="workflow_dispatch")) == 2


def test_push_only_processes_modified_pdf(monkeypatch, tmp_path):
    (tmp_path / "depot").mkdir()
    (tmp_path / "depot" / "a.pdf").write_bytes(b"a")
    (tmp_path / "depot" / "old.pdf").write_bytes(b"old")
    def fake_run(args, **kwargs):
        assert "--diff-filter=AMR" in args
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(args, 0, "depot/a.pdf\n", "")
    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    selected = pipeline.select_depot_pdfs(
        tmp_path, event="push", before_sha="1" * 40
    )
    assert [p.name for p in selected] == ["a.pdf"]


def test_push_with_no_pdf_changes_does_not_rerun_old_ocr(monkeypatch, tmp_path):
    (tmp_path / "depot").mkdir()
    (tmp_path / "depot" / "old.pdf").write_bytes(b"old")
    monkeypatch.setattr(
        pipeline.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    assert pipeline.select_depot_pdfs(
        tmp_path, event="push", before_sha="a" * 40
    ) == []


def test_unavailable_history_fails_closed(monkeypatch, tmp_path):
    (tmp_path / "depot").mkdir()
    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args[0], "missing ref")
    monkeypatch.setattr(pipeline.subprocess, "run", broken)
    with pytest.raises(subprocess.CalledProcessError):
        pipeline.select_depot_pdfs(
            tmp_path, event="push", before_sha="b" * 40
        )


def test_each_distinct_pdf_isolated_failure_does_not_hide_success(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"good")
    b.write_bytes(b"bad")
    def processor(pdf, root):
        if pdf.name == "b.pdf":
            raise ValueError("OCR language unrecognized")
        assert root == tmp_path / "out"
        return {"status": "CANDIDATE_NOT_VALIDATED", "approved_for_ai": False}
    report = pipeline.audit_depot([a, b, a], tmp_path / "out", processor=processor)
    assert report["selected_count"] == 3
    assert report["processed_count"] == 1
    assert report["blocked_count"] == 1
    assert report["duplicate_source_count"] == 1
    assert report["publication_state"] == "NOT_PUBLISHED"
    assert report["processed"][0]["approved_for_ai"] is False
    summary = json.loads((tmp_path / "out/full-audit/depot_summary.json").read_text())
    assert summary == report
