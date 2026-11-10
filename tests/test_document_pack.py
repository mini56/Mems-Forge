"""Real PDF -> reader -> source-preserving pack -> SQLite and tamper detection."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zipfile

import pymupdf
import pytest

from mems_forge.document_pack import build_document_pack, inspect_document_pack


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    source = tmp_path / "technical.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((55, 73), "Water temperature sensor at 25 Celsius")
        d.new_page().insert_text((55, 73), "ECU MEMS ignition controller")
        d.save(source)
    return source


def test_genuine_two_page_pdf_produces_searchable_verified_candidate(pdf, tmp_path):
    target = tmp_path / "pack.zip"
    result = build_document_pack(pdf, target)
    manifest = result["manifest"]
    assert target.is_file()
    assert manifest["source"]["pages"] == 2
    assert manifest["publication_state"] == "CANDIDATE_NOT_VALIDATED"
    assert manifest["blocking_reader_reviews"] == 0
    assert inspect_document_pack(target)["valid"]
    with zipfile.ZipFile(target) as pack:
        assert pack.read("source/document.pdf") == pdf.read_bytes()
        db = tmp_path / "source.sqlite"
        db.write_bytes(pack.read("data/knowledge.sqlite"))
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM source_pages").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM raw_blocks WHERE source_text LIKE ?",
            ("%temperature sensor%",),
        ).fetchone()[0] >= 1
        assert conn.execute("SELECT COUNT(*) FROM raw_spans").fetchone()[0] >= 1
    from mems_forge.db_tester import search_database
    result = search_database(db, "temperature sensor")
    assert result["count"] >= 1
    assert any(row["source_table"] == "raw_blocks" for row in result["results"])
    assert any(":p1" in row["fields"]["page_id"] for row in result["results"])


def test_corrupted_source_member_fails_integrity(pdf, tmp_path):
    target = tmp_path / "pack.zip"
    build_document_pack(pdf, target)
    modified = tmp_path / "modified.zip"
    with zipfile.ZipFile(target) as original, zipfile.ZipFile(modified, "w") as output:
        for name in original.namelist():
            data = original.read(name)
            if name == "source/document.pdf":
                data = b"replaced!"
            output.writestr(name, data)
    verdict = inspect_document_pack(modified)
    assert not verdict["valid"]
    assert verdict["error"] == "member_hash_invalid"


def test_existing_pack_never_overwritten(pdf, tmp_path):
    target = tmp_path / "pack.zip"
    build_document_pack(pdf, target)
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        build_document_pack(pdf, target)
    assert target.read_bytes() == original


def test_stale_reader_results_are_rejected(pdf, tmp_path):
    source = tmp_path / "other.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((55, 75), "Different")
        d.save(source)
    reader_root = tmp_path / "reader"
    from mems_forge.pdf_reader import analyze_pdf
    analyze_pdf(pdf, reader_root)
    # Replacing the PDF without rerunning the reader cannot reuse its
    # previously generated evidence. The pack must derive its own SHA.
    target = tmp_path / "pack.zip"
    result = build_document_pack(source, target, reader_root=reader_root)
    assert result["manifest"]["source"]["sha256"] != (
        __import__("hashlib").sha256(pdf.read_bytes()).hexdigest()
    )
