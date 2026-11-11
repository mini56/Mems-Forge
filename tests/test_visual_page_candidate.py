"""PDF visual-only pages must not disappear when OCR finds zero words."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pymupdf

from mems_forge.document_pack import build_document_pack, inspect_document_pack
from mems_forge.page_content import classify_page


TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
)


def _candidate_three_page_source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "technical.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        page = pdf.new_page()
        page.draw_rect(
            pymupdf.Rect(50, 50, 300, 300),
            fill=(0, 0, 0), color=(0, 0, 0),
        )
        pdf.new_page().insert_text((55, 75), "ECU MEMS")
        pdf.save(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = tmp_path / "ocr" / sha
    (directory / "pages").mkdir(parents=True)
    (directory / "raw").mkdir()
    with pymupdf.open(source) as pdf:
        for n in range(1, 4):
            visual = classify_page(pdf.load_page(n-1))
            text = "ECU" if n == 3 else ""
            tsv = TSV_HEADER
            words = []
            if n == 3:
                tsv += (
                    "5\t3\t1\t1\t1\t1\t10\t10\t50\t20\t93\tECU\n"
                )
                words = [{
                    "text": "ECU", "confidence": 93.0,
                    "left": 10, "top": 10, "width": 50, "height": 20,
                }]
            (directory / "raw" / f"page-{n:04d}.tsv.gz").write_bytes(
                gzip.compress(tsv.encode("utf-8"))
            )
            (directory / "pages" / f"page-{n:04d}.json").write_text(
                json.dumps({
                    "source_sha256": sha,
                    "page_number": n,
                    "pdf_page": {
                        "width_points": pdf[n-1].rect.width,
                        "height_points": pdf[n-1].rect.height,
                    },
                    "page_content": visual,
                    "text": text,
                    "language": "eng",
                    "confidence": {"mean": 93.0 if n == 3 else None},
                    "review_required": False,
                    "review_reasons": [],
                    "words": words,
                    "raw_ocr": {
                        "sha256": hashlib.sha256(
                            tsv.encode("utf-8")
                        ).hexdigest()
                    },
                }),
                encoding="utf-8",
            )
    output = tmp_path / "candidate.zip"
    build_document_pack(source, output, ocr_root=tmp_path / "ocr")
    return source, output


def test_blank_image_only_and_native_text_have_distinct_sqlite_states(tmp_path: Path):
    source, output = _candidate_three_page_source(tmp_path)
    assert inspect_document_pack(output)["valid"]
    with zipfile.ZipFile(output) as bundle:
        assert bundle.read("source/document.pdf") == source.read_bytes()
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["counts"]["ocr_page_content"] == 3
        dbfile = tmp_path / "candidate.sqlite"
        dbfile.write_bytes(bundle.read("data/knowledge.sqlite"))
    with sqlite3.connect(dbfile) as db:
        rows = db.execute(
            "SELECT p.physical_page_number,v.content_kind,v.ocr_skipped,"
            "v.inspection_status,v.visual_review_required,v.approved_for_ai "
            "FROM pack_ocr_page_content v "
            "JOIN source_pages p ON p.page_id=v.page_id "
            "ORDER BY p.physical_page_number"
        ).fetchall()
        assert rows == [
            (1, "BLANK_SCAN", 1, "CONFIRMED_BLANK_SCAN", 0, 0),
            (2, "RASTER_CONTENT", 0, "VISUAL_CONTENT_WITHOUT_OCR", 1, 0),
            (3, "NATIVE_TEXT", 0, "SOURCE_OCR_PRESENT", 0, 0),
        ]
    from mems_forge.db_tester import search_database
    result = search_database(dbfile, "VISUAL_CONTENT_WITHOUT_OCR")
    assert result["count"] == 1
    assert result["results"][0]["fields"]["visual_review_required"] == 1
