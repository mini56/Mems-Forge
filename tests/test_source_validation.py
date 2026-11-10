"""Source-proof validation must never silently approve a PDF-derived pack."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pymupdf

from mems_forge.document_pack import build_document_pack, inspect_document_pack
from mems_forge.source_validation import audit_source_pack, write_source_audit


def _pdf(path: Path, *, text: str = "ECU MEMS 1.9 sensor 25 C") -> Path:
    with pymupdf.open() as document:
        page = document.new_page()
        if text:
            page.insert_text((50, 90), text)
        document.save(path)
    return path


def test_source_pdf_and_sqlite_exact_text_parity(tmp_path: Path):
    pdf = _pdf(tmp_path / "source.pdf")
    pack = tmp_path / "document.zip"
    build_document_pack(pdf, pack)
    verdict = audit_source_pack(pack)
    assert verdict["pack_integrity_ok"] is True
    assert verdict["native_source_parity_ok"] is True
    assert verdict["pages_checked"] == 1
    assert verdict["native_text_pages"] == 1
    assert verdict["issues"] == []
    assert verdict["publication_state"] == "BLOCKED_SEMANTIC_VALIDATION"
    assert verdict["approved_for_ai"] is False
    assert verdict["approved_for_release"] is False

    report_path = tmp_path / "reports" / "source-validation.json"
    stored = write_source_audit(pack, report_path)
    assert json.loads(report_path.read_text(encoding="utf-8")) == stored


def test_reject_consistent_but_falsified_sqlite_text(tmp_path: Path):
    """Rehashing both SQLite and the manifest must not hide false source text."""
    pdf = _pdf(tmp_path / "source.pdf")
    original = tmp_path / "original.zip"
    build_document_pack(pdf, original)
    modified = tmp_path / "modified.zip"
    with zipfile.ZipFile(original) as source:
        members = {name: source.read(name) for name in source.namelist()}
    dbfile = tmp_path / "modified.sqlite"
    dbfile.write_bytes(members["data/knowledge.sqlite"])
    with sqlite3.connect(dbfile) as db:
        db.execute("UPDATE raw_blocks SET source_text='FABRICATED ECU FACT'")
        db.commit()
    members["data/knowledge.sqlite"] = dbfile.read_bytes()
    manifest = json.loads(members["manifest.json"])
    manifest["members_sha256"]["data/knowledge.sqlite"] = hashlib.sha256(
        members["data/knowledge.sqlite"]
    ).hexdigest()
    members["manifest.json"] = json.dumps(manifest).encode("utf-8")
    with zipfile.ZipFile(modified, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)

    assert inspect_document_pack(modified)["valid"] is True
    verdict = audit_source_pack(modified)
    assert verdict["pack_integrity_ok"] is True
    assert verdict["native_source_parity_ok"] is False
    assert any(x["code"] == "NATIVE_BLOCK_PARITY_FAILED" for x in verdict["issues"])
    assert verdict["approved_for_release"] is False


def test_no_native_text_without_ocr_is_not_complete(tmp_path: Path):
    pdf = _pdf(tmp_path / "image-only.pdf", text="")
    pack = tmp_path / "candidate.zip"
    build_document_pack(pdf, pack)
    result = audit_source_pack(pack)
    assert result["pack_integrity_ok"] is True
    assert result["native_source_parity_ok"] is False
    assert any(x["code"] == "NO_NATIVE_TEXT_OR_OCR" for x in result["issues"])


def test_invalid_pack_fails_closed(tmp_path: Path):
    pack = tmp_path / "candidate.zip"
    pack.write_bytes(b"not a zip")
    result = audit_source_pack(pack)
    assert result["pack_integrity_ok"] is False
    assert result["approved_for_release"] is False
    assert result["approved_for_ai"] is False
    assert any(x["code"] == "INVALID_PACK" for x in result["issues"])
