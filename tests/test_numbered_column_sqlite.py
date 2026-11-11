"""Numbered OCR remains searchable but never becomes technical truth.

The index uses the column OCR source as its only available positional scope.
There are deliberately no fabricated per-line PDF coordinates or ECU facts.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pymupdf

from mems_forge.document_pack import build_document_pack, inspect_document_pack
from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps


def _make_pack(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "technical.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((30, 100), "Original indexed manufacturer document")
        document.save(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    root = tmp_path / "ocr" / sha
    (root / "pages").mkdir(parents=True)
    (root / "raw").mkdir()
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t10\t50\t20\t90\tlambda\n"
    ).encode("utf-8")
    (root / "raw" / "page-0001.tsv.gz").write_bytes(gzip.compress(tsv))
    text = (
        "Remove\n1. Undo bolt\n2. Remove cap\n"
        "1. Install cap\n2. Tighten bolt\n"
    )
    (root / "pages" / "page-0001.json").write_text(
        json.dumps({
            "source_sha256": sha,
            "page_number": 1,
            "pdf_page": {"width_points": 595.0, "height_points": 842.0},
            "text": "lambda",
            "language": "eng",
            "confidence": {"mean": 90.0},
            "review_required": True,
            "review_reasons": [],
            "raw_ocr": {"sha256": hashlib.sha256(tsv).hexdigest()},
            "words": [{
                "text": "lambda", "confidence": 90.0,
                "left": 10, "top": 10, "width": 50, "height": 20,
            }],
            "adaptive_extraction": {
                "recovered_list": None,
                "columns": {
                    "left": {
                        "text": text,
                        "candidate_mode": "psm4",
                        "bbox_pdf": [30.0, 30.0, 240.0, 400.0],
                        "phase_segments": _phase_bounded_numbered_steps(text),
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    pack = tmp_path / "candidate.zip"
    build_document_pack(source, pack, ocr_root=tmp_path / "ocr")
    return source, pack


def test_candidate_sqlite_indexes_numbered_lines_with_real_column_scope(
    tmp_path: Path,
):
    _, pack = _make_pack(tmp_path)
    assert inspect_document_pack(pack)["valid"]
    with zipfile.ZipFile(pack) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["counts"]["ocr_numbered_segments"] == 2
        assert manifest["counts"]["ocr_numbered_lines"] == 4
        dbfile = tmp_path / "source.sqlite"
        dbfile.write_bytes(archive.read("data/knowledge.sqlite"))
    with sqlite3.connect(dbfile) as db:
        rows = db.execute(
            "SELECT s.phase,s.source_kind,s.review_required,l.step_number,"
            "l.source_text,l.source_bbox_scope,l.approved_for_ai "
            "FROM pack_ocr_numbered_lines l "
            "JOIN pack_ocr_numbered_segments s ON s.segment_id=l.segment_id "
            "ORDER BY s.segment_index,l.line_index"
        ).fetchall()
        assert rows == [
            ("remove", "numbered_lines_under_explicit_phase", 0, 1,
             "1. Undo bolt", "COLUMN_ROI_ONLY", 0),
            ("remove", "numbered_lines_under_explicit_phase", 0, 2,
             "2. Remove cap", "COLUMN_ROI_ONLY", 0),
            ("unassigned", "numbered_lines_unclassified", 1, 1,
             "1. Install cap", "COLUMN_ROI_ONLY", 0),
            ("unassigned", "numbered_lines_unclassified", 1, 2,
             "2. Tighten bolt", "COLUMN_ROI_ONLY", 0),
        ]
        assert db.execute(
            "SELECT COUNT(*) FROM structured_entities"
        ).fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    from mems_forge.db_tester import search_database
    searched = search_database(dbfile, "Install cap")
    assert any(r["source_table"] == "pack_ocr_numbered_lines"
               for r in searched["results"])


def test_rehashed_sqlite_with_fabricated_step_is_rejected(tmp_path: Path):
    _, pack = _make_pack(tmp_path)
    with zipfile.ZipFile(pack) as original:
        members = {filename: original.read(filename)
                   for filename in original.namelist()}
    dbfile = tmp_path / "tamper.sqlite"
    dbfile.write_bytes(members["data/knowledge.sqlite"])
    with sqlite3.connect(dbfile) as db:
        db.execute(
            "UPDATE pack_ocr_numbered_lines SET source_text=? "
            "WHERE source_text='2. Tighten bolt'",
            ("2. Replace ECU with invented part",),
        )
        db.commit()
    members["data/knowledge.sqlite"] = dbfile.read_bytes()
    manifest = json.loads(members["manifest.json"])
    manifest["members_sha256"]["data/knowledge.sqlite"] = hashlib.sha256(
        members["data/knowledge.sqlite"]
    ).hexdigest()
    members["manifest.json"] = json.dumps(manifest).encode("utf-8")
    tampered = tmp_path / "rehash-tampered.zip"
    with zipfile.ZipFile(tampered, "w") as out:
        for name, payload in members.items():
            out.writestr(name, payload)
    inspected = inspect_document_pack(tampered)
    assert not inspected["valid"]
    assert inspected["error"] == "ocr_numbered_line_not_from_source"
