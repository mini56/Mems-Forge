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


def test_staging_and_searching_new_pack_does_not_touch_application_db(pdf, tmp_path):
    from mems_forge.local_update import (
        stage_document_pack, list_staged, search_staged,
    )
    pack = tmp_path / "candidate.zip"
    store = tmp_path / "appdata"
    build_document_pack(pdf, pack)
    other = tmp_path / "ecu_mems_reference.sqlite"
    other.write_bytes(b"EXISTING_DATABASE_BYTES_MUST_BE_UNCHANGED")
    result = stage_document_pack(pack, store)
    assert result["action"] == "INSTALLED_PREVIEW"
    assert result["pages"] == 2
    assert other.read_bytes() == b"EXISTING_DATABASE_BYTES_MUST_BE_UNCHANGED"
    assert len(list_staged(store)) == 1
    found = search_staged(store, "temperature sensor")
    assert found["count"] >= 1
    assert all(not r["approved_for_ai"] for r in found["results"])
    assert any(str(r["fields"].get("page_id", "")).endswith(":p1")
               for r in found["results"])
    repeat = stage_document_pack(pack, store)
    assert repeat["action"] == "ALREADY_PRESENT"


def test_staging_rejects_corrupt_pack_and_unchanged_existing_state(pdf, tmp_path):
    from mems_forge.local_update import stage_document_pack, list_staged
    pack = tmp_path / "candidate.zip"
    store = tmp_path / "updates"
    build_document_pack(pdf, pack)
    valid = stage_document_pack(pack, store)
    corrupted = tmp_path / "corrupted.zip"
    with zipfile.ZipFile(pack) as a, zipfile.ZipFile(corrupted, "w") as b:
        for name in a.namelist():
            content = a.read(name)
            if name == "source/document.pdf":
                content += b"tampered"
            b.writestr(name, content)
    with pytest.raises(ValueError, match="Rejected damaged"):
        stage_document_pack(corrupted, store)
    unchanged = list_staged(store)
    assert len(unchanged) == 1
    assert unchanged[0]["pack_sha256"] == valid["pack_sha256"]


def test_installed_file_tampering_is_detected(pdf, tmp_path):
    from mems_forge.local_update import stage_document_pack, list_staged, search_staged
    pack = tmp_path / "candidate.zip"
    store = tmp_path / "store"
    build_document_pack(pdf, pack)
    result = stage_document_pack(pack, store)
    source = store / "preview" / result["source_sha256"] / "source" / "document.pdf"
    source.write_bytes(b"file modified")
    listed = list_staged(store)
    assert listed[0]["error"] == "MEMBER_HASH_MISMATCH"
    assert search_staged(store, "sensor")["count"] == 0


def test_stage_does_not_accept_unexpected_archive_members(pdf, tmp_path):
    from mems_forge.local_update import stage_document_pack
    pack = tmp_path / "candidate.zip"
    build_document_pack(pdf, pack)
    with zipfile.ZipFile(pack, "a") as bundle:
        bundle.writestr("../../outside.txt", "bad")
    with pytest.raises(ValueError, match="Rejected damaged"):
        stage_document_pack(pack, tmp_path / "store")
    assert not (tmp_path / "outside.txt").exists()


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


def test_full_ocr_text_and_raw_tsv_are_searchable_but_not_approved(pdf, tmp_path):
    import gzip
    import hashlib
    from mems_forge.pdf_reader import sha256_file
    sha = sha256_file(pdf)
    source = tmp_path / "ocr" / sha
    (source / "pages").mkdir(parents=True)
    (source / "raw").mkdir()
    for page in (1, 2):
        tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
               "left\ttop\twidth\theight\tconf\ttext\n"
               f"5\t{page}\t1\t1\t1\t1\t10\t10\t50\t20\t93.1\tlambda\n").encode()
        (source / "raw" / f"page-{page:04d}.tsv.gz").write_bytes(gzip.compress(tsv))
        (source / "pages" / f"page-{page:04d}.json").write_text(
            json.dumps({
                "source_sha256": sha, "page_number": page,
                "text": "lambda", "language": "eng",
                "confidence": {"mean": 93.1}, "review_required": False,
                "raw_ocr": {"sha256": hashlib.sha256(tsv).hexdigest()},
                "adaptive_extraction": ({
                    "recovered_list": {
                        "roi_pdf": [10.0, 10.0, 155.0, 70.0],
                        "candidate_text": "1. Recovered coolant sensor",
                        "independent_render_readings_agree": True,
                    },
                    "columns": {},
                } if page == 1 else None),
                "words": [{"text": "lambda", "confidence": 93.1,
                           "left": 10, "top": 10, "width": 50, "height": 20}],
            }), encoding="utf-8",
        )
    target = tmp_path / "with-ocr.zip"
    result = build_document_pack(pdf, target, ocr_root=tmp_path / "ocr")
    assert result["manifest"]["counts"]["ocr_pages"] == 2
    assert result["manifest"]["counts"]["ocr_words"] == 2
    assert result["manifest"]["counts"]["ocr_variants"] == 1
    assert result["manifest"]["publication_state"] == "CANDIDATE_NOT_VALIDATED"
    assert inspect_document_pack(target)["valid"]
    with zipfile.ZipFile(target) as bundle:
        assert "ocr/page-0001.tsv.gz" in bundle.namelist()
        db = tmp_path / "ocr.sqlite"
        db.write_bytes(bundle.read("data/knowledge.sqlite"))
    from mems_forge.db_tester import search_database
    result = search_database(db, "lambda")
    assert result["count"] == 2
    assert all(x["source_table"] == "pack_ocr_pages" for x in result["results"])
    assert result["results"][0]["fields"]["page_id"].endswith(":p1")
    adapted = search_database(db, "Recovered coolant")
    assert adapted["count"] == 1
    assert adapted["results"][0]["source_table"] == "pack_ocr_variants"
    assert adapted["results"][0]["fields"]["crosschecked"] == 1
    assert adapted["results"][0]["fields"]["approved_for_ai"] == 0


def test_incomplete_ocr_input_does_not_produce_a_pack(pdf, tmp_path):
    root = tmp_path / "missing"
    root.mkdir()
    target = tmp_path / "blocked.zip"
    with pytest.raises(ValueError, match="Missing verified OCR page"):
        build_document_pack(pdf, target, ocr_root=root)
    assert not target.exists()


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
