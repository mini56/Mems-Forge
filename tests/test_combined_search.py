"""Proof that new PDFs remain searchable beside the installed base, without writes."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pymupdf

from mems_forge.document_pack import build_document_pack
from mems_forge.local_update import (
    search_all_sources, stage_document_pack,
)


def _existing_reference(install: Path) -> tuple[Path, Path]:
    baseline = install / "MEMSLibrary_Pack_001" / "knowledge.sqlite"
    expert = install / "database" / "expert" / "ia_mems_reference_r20.sqlite"
    baseline.parent.mkdir(parents=True)
    expert.parent.mkdir(parents=True)
    with sqlite3.connect(baseline) as db:
        db.execute(
            "CREATE TABLE memslibrary_search("
            "title TEXT, body TEXT, document_key TEXT, entity_key TEXT)"
        )
        db.execute(
            "INSERT INTO memslibrary_search VALUES(?,?,?,?)",
            ("MEMS baseline", "lambda reference already installed", "RAVE47", "one"),
        )
    with sqlite3.connect(expert) as db:
        db.execute("CREATE TABLE mems_knowledge_item(topic TEXT,source_text TEXT)")
    return baseline, expert


def test_combined_query_returns_existing_and_new_unverified_pdf(tmp_path):
    install = tmp_path / "application"
    existing, expert = _existing_reference(install)
    source = tmp_path / "addition.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 75), "lambda new factory document")
        doc.save(source)
    pack = tmp_path / "new-pack.zip"
    build_document_pack(source, pack)
    store = tmp_path / "user-app-data"
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (existing, expert)
    }
    stage_document_pack(pack, store)
    result = search_all_sources(install, store, "lambda", limit=10)
    assert result["errors"] == []
    assert result["existing_matches"] >= 1
    assert result["preview_matches"] >= 1
    assert {row["scope"] for row in result["results"]} == {
        "EXISTING_REFERENCE", "DOCUMENT_UPDATE_PREVIEW",
    }
    preview = next(
        row for row in result["results"]
        if row["scope"] == "DOCUMENT_UPDATE_PREVIEW"
    )
    assert preview["approved_for_ai"] is False
    assert preview["validation"] == "UNVERIFIED_SOURCE_TEXT"
    assert preview["source_pdf"].endswith("document.pdf")
    assert preview["fields"]["page_id"].endswith(":p1")
    assert result["application_databases_modified"] is False
    for path, digest in before.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_corrupted_preview_is_hidden_but_installed_base_survives(tmp_path):
    install = tmp_path / "application"
    _existing_reference(install)
    source = tmp_path / "addition.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 75), "lambda new source")
        doc.save(source)
    pack = tmp_path / "pack.zip"
    build_document_pack(source, pack)
    store = tmp_path / "appdata"
    installed = stage_document_pack(pack, store)
    staged_pdf = store / "preview" / installed["source_sha256"] / "source/document.pdf"
    staged_pdf.write_bytes(b"tampered")
    found = search_all_sources(install, store, "lambda")
    assert found["existing_matches"] >= 1
    assert found["preview_matches"] == 0
    assert found["errors"]
    assert all(x["scope"] == "EXISTING_REFERENCE" for x in found["results"])
