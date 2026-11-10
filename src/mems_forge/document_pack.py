"""Build a verifiable, source-preserving document pack from MEMS Forge's reader.

A raw pack is NOT a validated technical-knowledge publication. It includes
the unmodified PDF and a searchable provenance-backed SQLite index. OCR and
uncertain interpretation never become approved facts by mere extraction.
"""
from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from . import __version__
from .database import initialize_database
from .pdf_reader import analyze_pdf, sha256_file


def records(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _bbox(value):
    if not isinstance(value, list) or len(value) != 4:
        return (None, None, None, None)
    return tuple(value)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_document_pack(
    pdf_path: Path, output_path: Path, *, reader_root: Path | None = None
) -> dict:
    """Produce a candidate pack only, never modify the published database."""
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise ValueError("An existing PDF file is required")
    source_sha = sha256_file(pdf_path)
    with tempfile.TemporaryDirectory(prefix="mems-forge-pack-") as temporary:
        tmp = Path(temporary)
        analysis_root = reader_root if reader_root is not None else tmp / "reader"
        summary_file = Path(analysis_root) / source_sha / "summary.json"
        if summary_file.is_file():
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
        else:
            summary = analyze_pdf(pdf_path, Path(analysis_root))
        if (summary["source"]["sha256"] != source_sha or
                summary["source"]["size_bytes"] != pdf_path.stat().st_size):
            raise ValueError("Reader output does not belong to this PDF")
        data_root = Path(analysis_root) / source_sha
        expected_pages = int(summary["source"]["page_count"])
        if expected_pages < 1:
            raise ValueError("Empty or invalid source document")
        database_file = tmp / "knowledge.sqlite"
        initialize_database(database_file, __version__)
        revision = "REV_" + source_sha
        document = "DOC_" + source_sha[:24]
        count = {"pages": 0, "blocks": 0, "spans": 0, "image_occurrences": 0, "reviews": 0}
        with closing(sqlite3.connect(database_file)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "INSERT INTO documents(document_id,title,document_type) VALUES(?,?,?)",
                (document, pdf_path.name, "PDF"),
            )
            db.execute(
                "INSERT INTO document_revisions(revision_id,document_id,"
                "source_filename,sha256,source_size,page_count,forge_version)"
                " VALUES(?,?,?,?,?,?,?)",
                (revision, document, pdf_path.name, source_sha,
                 pdf_path.stat().st_size, expected_pages, __version__),
            )
            for page in records(data_root / "pages.jsonl.gz"):
                n = int(page["page_number"])
                if n != count["pages"] + 1:
                    raise ValueError(f"Non-contiguous PDF page {n}")
                db.execute(
                    "INSERT INTO source_pages(page_id,revision_id,physical_page_number,"
                    "printed_page_label,width,height,rotation,content_kind)"
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (f"{revision}:p{n}", revision, n, page.get("page_label"),
                     page.get("width"), page.get("height"), page.get("rotation"),
                     page.get("page_kind")),
                )
                count["pages"] += 1
            if count["pages"] != expected_pages:
                raise ValueError("Missing pages in reader evidence")
            for block in records(data_root / "raw_blocks.jsonl.gz"):
                n = int(block["page_number"])
                ix = int(block["block_index"])
                b = _bbox(block.get("bbox"))
                db.execute(
                    "INSERT INTO raw_blocks(raw_block_id,page_id,extraction_method,"
                    "block_kind,source_text,bbox_x0,bbox_y0,bbox_x1,bbox_y1,"
                    "geometric_order,engine_version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (f"{revision}:p{n}:b{ix}", f"{revision}:p{n}",
                     "pymupdf_native", str(block.get("block_type", "unknown")),
                     block.get("text", ""), *b, ix, __version__),
                )
                count["blocks"] += 1
            for span in records(data_root / "raw_spans.jsonl.gz"):
                n = int(span["page_number"])
                i = int(span["block_index"])
                line = int(span["line_index"])
                ix = int(span["span_index"])
                b = _bbox(span.get("bbox"))
                db.execute(
                    "INSERT INTO raw_spans(raw_span_id,raw_block_id,source_text,"
                    "bbox_x0,bbox_y0,bbox_x1,bbox_y1,font_name,font_size,"
                    "span_order) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (f"{revision}:p{n}:b{i}:l{line}:s{ix}", f"{revision}:p{n}:b{i}",
                     span.get("text", ""), *b, span.get("font"),
                     span.get("size"), ix),
                )
                count["spans"] += 1
            db.execute(
                "CREATE TABLE pack_image_occurrences("
                "revision_id TEXT NOT NULL,page_number INTEGER NOT NULL,"
                "image_index INTEGER NOT NULL,sha256_or_digest TEXT,"
                "bbox_json TEXT, width INTEGER,height INTEGER,"
                "PRIMARY KEY(revision_id,page_number,image_index))"
            )
            for visual in records(data_root / "images.jsonl.gz"):
                db.execute(
                    "INSERT INTO pack_image_occurrences VALUES(?,?,?,?,?,?,?)",
                    (revision, visual["page_number"], visual["image_index"],
                     visual.get("digest"), json.dumps(visual.get("bbox")),
                     visual.get("width"), visual.get("height")),
                )
                count["image_occurrences"] += 1
            for item in records(data_root / "review_items.jsonl.gz"):
                n = int(item["page_number"])
                db.execute(
                    "INSERT INTO review_items(review_id,revision_id,page_id,severity,"
                    "reason_code,reason_text,status) VALUES(?,?,?,?,?,?,?)",
                    (item["review_id"], revision, f"{revision}:p{n}",
                     item["severity"], item["code"],
                     item["reason"],
                     "pending"),
                )
                count["reviews"] += 1
            db.execute(
                "INSERT OR REPLACE INTO database_metadata(key,value) VALUES"
                "('pack_kind','raw_document_candidate'),"
                "('publication_state','blocked_semantic_review')"
            )
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Candidate SQLite failed integrity check")
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Candidate SQLite foreign key violations")
            db.commit()
        # Even native-text PDFs still need semantic/applicability review.
        # Their raw text can be searched without asserting technical validity.
        pdf_bytes = pdf_path.read_bytes()
        db_bytes = database_file.read_bytes()
        manifest = {
            "format": "mems-forge-document-pack", "format_version": 1,
            "forge_version": __version__,
            "pack_id": "DOC_" + source_sha[:24],
            "source": {
                "filename": pdf_path.name, "sha256": source_sha,
                "size_bytes": len(pdf_bytes), "pages": expected_pages,
            },
            "members_sha256": {
                "source/document.pdf": _hash(pdf_bytes),
                "data/knowledge.sqlite": _hash(db_bytes),
            },
            "counts": count,
            "publication_state": "CANDIDATE_NOT_VALIDATED",
            "reason": (
                "Native text and visual occurrence inventory only; "
                "semantic/applicability validation not complete"
            ),
            "blocking_reader_reviews": summary["counts"]["blocking_review_items"],
            "available_for": ["direct_raw_search", "source_pdf_viewer"],
            "not_approved_for": ["validated_technical_facts", "automatic_ai_answers"],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise FileExistsError(f"Pack exists: {output_path}")
        entries = {
            "manifest.json": (json.dumps(
                manifest, sort_keys=True, ensure_ascii=False, indent=2
            ) + "\n").encode("utf-8"),
            "source/document.pdf": pdf_bytes,
            "data/knowledge.sqlite": db_bytes,
        }
        with zipfile.ZipFile(output_path, "x", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as pack:
            for filename in sorted(entries):
                info = zipfile.ZipInfo(filename, date_time=(2024, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                pack.writestr(info, entries[filename])
        checked = inspect_document_pack(output_path)
        if not checked["valid"]:
            output_path.unlink(missing_ok=True)
            raise ValueError("Candidate package verification failed: " + str(checked))
        return {"path": str(output_path), "manifest": manifest, "validation": checked}


def inspect_document_pack(path: Path) -> dict:
    """Validate a standalone candidate without extracting files or modifying SQLite."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            expected = {"manifest.json", "source/document.pdf", "data/knowledge.sqlite"}
            if set(archive.namelist()) != expected:
                return {"valid": False, "error": "unexpected_or_missing_members"}
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "mems-forge-document-pack":
                return {"valid": False, "error": "unknown_format"}
            if manifest.get("format_version") != 1:
                return {"valid": False, "error": "unknown_format_version"}
            for member, expected_sha in manifest["members_sha256"].items():
                if member not in expected or _hash(archive.read(member)) != expected_sha:
                    return {"valid": False, "error": "member_hash_invalid", "member": member}
            if _hash(archive.read("source/document.pdf")) != manifest["source"]["sha256"]:
                return {"valid": False, "error": "source_sha256_invalid"}
            with tempfile.TemporaryDirectory(prefix="mems-forge-verify-") as tmp:
                db_file = Path(tmp) / "candidate.sqlite"
                db_file.write_bytes(archive.read("data/knowledge.sqlite"))
                with closing(sqlite3.connect(db_file)) as db:
                    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
                    pages = db.execute("SELECT COUNT(*) FROM source_pages").fetchone()[0]
                    metadata = db.execute(
                        "SELECT value FROM database_metadata WHERE key='publication_state'"
                    ).fetchone()
                    if (integrity != "ok" or
                            db.execute("PRAGMA foreign_key_check").fetchall() or
                            pages != manifest["source"]["pages"] or
                            metadata is None or metadata[0] != "blocked_semantic_review"):
                        return {"valid": False, "error": "sqlite_validation_failed"}
            return {
                "valid": True, "pack_id": manifest["pack_id"],
                "source_pages": manifest["source"]["pages"],
                "state": manifest["publication_state"],
            }
    except (OSError, ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
        return {"valid": False, "error": type(exc).__name__ + ": " + str(exc)}
