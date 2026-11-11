"""Safety notices remain distinct source evidence, never silently approved steps."""
from __future__ import annotations

from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pymupdf

from mems_forge.ocr_reader import _parse_words
from mems_forge.ocr_layout import _line_records
from mems_forge.procedure_fragments import analyze_page_fragments
from mems_forge.document_pack import build_document_pack, inspect_document_pack


def _source_line(n, text, x0, y0, x1, y1, region="left"):
    return {
        "region": region,
        "source_line_key": [1, 1, n],
        "bbox": [x0, y0, x1, y1],
        "text": text,
        "mean_confidence": 94.0,
    }


def test_notice_is_not_added_to_procedure_even_with_other_repair_on_page():
    layout = {
        "page_number": 1,
        "lines": [
            _source_line(1, "Remove", 20, 20, 120, 40),
            _source_line(2, "1. Raise front of vehicle.", 20, 50, 380, 70),
            _source_line(3, "WARNING: Support on safety stands.", 20, 80, 490, 99),
            _source_line(4, "2. Remove wheel.", 20, 110, 330, 129),
            _source_line(5, "Service Repair No. 68.15.16.", 500, 40, 900, 60, "right"),
        ],
    }
    structure = {
        "page_number": 1,
        "procedures": [{"region": "left", "repair_number": "68.15.01"}],
    }
    result = analyze_page_fragments(layout, structure)
    assert result["fragment_count"] == 0
    assert result["source_notice_count"] == 1
    entry = result["source_notices"][0]
    assert entry["kind"] == "WARNING"
    assert entry["source_text"] == "WARNING: Support on safety stands."
    assert entry["relationship_to_procedure"] == "UNRESOLVED"
    assert entry["approved_for_ai"] is False
    assert entry["source_lines"][0]["source_line_key"] == [1, 1, 3]


def test_wrapped_warning_and_nearby_numbered_step_are_not_mixed():
    layout = {
        "page_number": 1,
        "lines": [
            _source_line(1, "WARNING: Keep the vehicle", 20, 20, 390, 40),
            _source_line(2, "securely supported.", 60, 43, 350, 60),
            _source_line(3, "1. Remove road wheel.", 20, 71, 400, 92),
            _source_line(4, "Note: Record position of shim.", 20, 110, 470, 130),
        ],
    }
    result = analyze_page_fragments(
        layout, {"page_number": 1, "procedures": []},
    )
    assert [x["kind"] for x in result["source_notices"]] == ["WARNING", "NOTE"]
    assert result["source_notices"][0]["source_line_count"] == 2
    assert result["source_notices"][0]["source_text"] == (
        "WARNING: Keep the vehicle securely supported."
    )
    assert result["source_notices"][1]["source_line_count"] == 1
    assert "1. Remove road wheel." not in result["source_notices"][0]["source_text"]


def _source_pack(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "AKM7169ENG-manual.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text(
            (40, 95), "WARNING: Support on safety stands.",
        )
        document.save(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()

    tokens = ["WARNING:", "Support", "on", "safety", "stands."]
    entries = []
    x = 30
    for n, token in enumerate(tokens, 1):
        width = 8 * len(token)
        entries.append(
            f"5\t1\t1\t1\t1\t{n}\t{x}\t80\t{width}\t20\t94\t{token}"
        )
        x += width + 7
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n" +
        "\n".join(entries) + "\n"
    ).encode("utf-8")
    words, errors = _parse_words(tsv.decode("utf-8"), 1)
    assert not errors
    record = asdict(words[0])
    assert record["page_number"] == 1
    source_line = _line_records([asdict(w) for w in words], region="left")[0]
    assert source_line["text"] == "WARNING: Support on safety stands."
    extracted = analyze_page_fragments(
        {"page_number": 1, "lines": [source_line]},
        {"page_number": 1, "procedures": []},
    )

    ocr_root = tmp_path / "ocr" / sha
    (ocr_root / "pages").mkdir(parents=True)
    (ocr_root / "raw").mkdir(parents=True)
    (ocr_root / "raw" / "page-0001.tsv.gz").write_bytes(gzip.compress(tsv))
    (ocr_root / "pages" / "page-0001.json").write_text(
        json.dumps({
            "page_number": 1,
            "source_sha256": sha,
            "pdf_page": {"width_points": 595.0, "height_points": 842.0},
            "text": source_line["text"],
            "language": "eng",
            "confidence": {"mean": 94.0},
            "review_required": True,
            "review_reasons": [],
            "raw_ocr": {"sha256": hashlib.sha256(tsv).hexdigest()},
            "words": [asdict(w) for w in words],
        }),
        encoding="utf-8",
    )
    fragment_root = tmp_path / "fragments"
    pages = fragment_root / sha / "pages"
    pages.mkdir(parents=True)
    (pages / "page-0001.json").write_text(
        json.dumps(extracted), encoding="utf-8",
    )
    (fragment_root / "summary.json").write_text(
        json.dumps({
            "source_pdf_sha256": sha,
            "automatic_procedure_merges": 0,
            "publication_gate": "BLOCKED_REVIEW",
            "original_physical_page_map": {"1": 304},
            "cross_page_link_candidate_count": 0,
            "cross_page_link_candidates": [],
            "source_notice_count": 1,
        }),
        encoding="utf-8",
    )
    pack = tmp_path / "candidate.zip"
    build_document_pack(
        source, pack,
        ocr_root=tmp_path / "ocr",
        fragment_root=fragment_root,
    )
    return source, pack


def test_source_warning_is_in_sqlite_with_real_ocr_lineage(tmp_path: Path):
    source, pack = _source_pack(tmp_path)
    verdict = inspect_document_pack(pack)
    assert verdict["valid"], verdict
    with zipfile.ZipFile(pack) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["counts"]["safety_notice_candidates"] == 1
        assert archive.read("source/document.pdf") == source.read_bytes()
        path = tmp_path / "candidate.sqlite"
        path.write_bytes(archive.read("data/knowledge.sqlite"))
    with sqlite3.connect(path) as db:
        stored = db.execute(
            "SELECT original_physical_page,source_region,notice_kind,"
            "source_text,source_line_count,relationship_to_procedure,"
            "validation_state,approved_for_ai FROM pack_source_notices"
        ).fetchone()
        assert stored == (
            304, "left", "WARNING", "WARNING: Support on safety stands.",
            1, "UNRESOLVED", "unresolved", 0,
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    from mems_forge.db_tester import search_database
    found = search_database(path, "Support on safety")
    assert any(
        x["source_table"] == "pack_source_notices"
        for x in found["results"]
    )


def test_forged_warning_rejected_even_after_sha_recalculated(tmp_path: Path):
    _, pack = _source_pack(tmp_path)
    with zipfile.ZipFile(pack) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    path = tmp_path / "tampered.sqlite"
    path.write_bytes(members["data/knowledge.sqlite"])
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE pack_source_notices "
            "SET source_text='WARNING: Safe to ignore vehicle support.'"
        )
        db.commit()
    members["data/knowledge.sqlite"] = path.read_bytes()
    manifest = json.loads(members["manifest.json"])
    manifest["members_sha256"]["data/knowledge.sqlite"] = hashlib.sha256(
        members["data/knowledge.sqlite"]
    ).hexdigest()
    members["manifest.json"] = json.dumps(manifest).encode("utf-8")
    corrupt = tmp_path / "forged.zip"
    with zipfile.ZipFile(corrupt, "w") as out:
        for name, content in members.items():
            out.writestr(name, content)
    result = inspect_document_pack(corrupt)
    assert result["valid"] is False
    assert result["error"] == "source_notice_not_preserved"
