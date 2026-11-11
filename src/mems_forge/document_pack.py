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
from .ocr_reader import _parse_words, _words_to_text


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


def _adaptive_ocr_evidence(page_data: dict, page_number: int) -> tuple[list, list]:
    """Separate legible source-backed OCR variants from unresolved image regions.

    A missing OCR reading is not a technical fact, and it must not abort a
    candidate's entire source inventory if the original page explicitly
    records the failure. It becomes a page/region-scoped issue in SQLite.
    Any unaccounted-for empty reading or broken geometry is a hard error.
    """
    extraction = page_data.get("adaptive_extraction") or {}
    observations = []
    recovered = extraction.get("recovered_list")
    if recovered:
        observations.append((
            "recovered_numbered_list", "psm6_200dpi_400dpi",
            recovered.get("roi_pdf"), recovered.get("candidate_text"),
            bool(recovered.get("independent_render_readings_agree")),
        ))
    for side, reading in sorted((extraction.get("columns") or {}).items()):
        if side not in ("left", "right") or not isinstance(reading, dict):
            raise ValueError(f"Unexpected adaptive OCR region on page {page_number}")
        observations.append((
            "column_" + side,
            str(reading.get("candidate_mode") or "unknown"),
            reading.get("bbox_pdf"), reading.get("text"), False,
        ))
    declared = set(page_data.get("adaptive_empty_regions") or [])
    recorded_reasons = page_data.get("review_reasons") or []
    variants = []
    issues = []
    for region, method, bbox, candidate_text, crosschecked in observations:
        if (not isinstance(bbox, list) or len(bbox) != 4 or
                not all(type(value) in (float, int) for value in bbox) or
                bbox[0] >= bbox[2] or bbox[1] >= bbox[3] or
                not isinstance(candidate_text, str)):
            raise ValueError(
                f"Invalid adaptive OCR provenance on page {page_number}: {region}"
            )
        if candidate_text.strip():
            if region in declared:
                raise ValueError(
                    f"Contradictory adaptive OCR review on page {page_number}: {region}"
                )
            variants.append((region, method, bbox, candidate_text, crosschecked))
            continue
        # Fail closed for missing text, without confusing "nothing recognized"
        # with evidence that a scanned figure contains no information.
        if (region not in declared or not page_data.get("review_required") or
                "adaptive_region_without_text" not in recorded_reasons):
            raise ValueError(
                f"Unrecorded empty adaptive OCR region on page {page_number}: {region}"
            )
        issues.append((region, method, bbox, "ADAPTIVE_REGION_NO_TEXT"))
    if declared != {item[0] for item in issues}:
        raise ValueError(
            f"Unmatched adaptive OCR review regions on page {page_number}"
        )
    return variants, issues


def build_document_pack(
    pdf_path: Path, output_path: Path, *, reader_root: Path | None = None,
    ocr_root: Path | None = None
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
        count = {"pages": 0, "blocks": 0, "spans": 0, "image_occurrences": 0,
                 "reviews": 0, "ocr_pages": 0, "ocr_words": 0, "ocr_variants": 0,
                 "ocr_issues": 0}
        ocr_members = {}
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
                "CREATE TABLE pack_ocr_pages("
                "page_id TEXT PRIMARY KEY REFERENCES source_pages(page_id),"
                "text TEXT NOT NULL,language TEXT,confidence_mean REAL,"
                "review_required INTEGER NOT NULL,raw_tsv_sha256 TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE pack_ocr_words("
                "page_id TEXT NOT NULL REFERENCES pack_ocr_pages(page_id),"
                "word_index INTEGER NOT NULL,text TEXT NOT NULL,confidence REAL,"
                "left_px INTEGER,top_px INTEGER,width_px INTEGER,height_px INTEGER,"
                "PRIMARY KEY(page_id,word_index))"
            )
            db.execute(
                "CREATE TABLE pack_ocr_variants("
                "variant_id TEXT PRIMARY KEY,"
                "page_id TEXT NOT NULL REFERENCES pack_ocr_pages(page_id),"
                "region TEXT NOT NULL,method TEXT NOT NULL,"
                "source_bbox_pdf_json TEXT NOT NULL,"
                "candidate_text TEXT NOT NULL,"
                "crosschecked INTEGER NOT NULL CHECK(crosschecked IN (0,1)),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 CHECK(approved_for_ai=0))"
            )
            db.execute(
                "CREATE TABLE pack_ocr_issues("
                "issue_id TEXT PRIMARY KEY,"
                "page_id TEXT NOT NULL REFERENCES pack_ocr_pages(page_id),"
                "region TEXT NOT NULL,method TEXT NOT NULL,"
                "source_bbox_pdf_json TEXT NOT NULL,"
                "reason_code TEXT NOT NULL,"
                "validation_state TEXT NOT NULL DEFAULT 'unresolved' "
                "CHECK(validation_state='unresolved'))"
            )
            if ocr_root is not None:
                directory = Path(ocr_root) / source_sha
                pages_dir = directory / "pages"
                raw_dir = directory / "raw"
                for n in range(1, expected_pages + 1):
                    pagefile = pages_dir / f"page-{n:04d}.json"
                    if not pagefile.is_file():
                        raise ValueError(f"Missing verified OCR page {n}")
                    page_data = json.loads(pagefile.read_text(encoding="utf-8"))
                    if (page_data.get("source_sha256") != source_sha or
                            int(page_data.get("page_number", -1)) != n):
                        raise ValueError(f"OCR provenance mismatch on page {n}")
                    raw = page_data.get("raw_ocr") or {}
                    payload = raw_dir / f"page-{n:04d}.tsv.gz"
                    if not payload.is_file():
                        raise ValueError(f"OCR TSV source absent for page {n}")
                    raw_gzip = payload.read_bytes()
                    if _hash(gzip.decompress(raw_gzip)) != raw.get("sha256"):
                        raise ValueError(f"OCR TSV evidence hash mismatch on page {n}")
                    member = f"ocr/page-{n:04d}.tsv.gz"
                    ocr_members[member] = raw_gzip
                    page_id = f"{revision}:p{n}"
                    db.execute(
                        "INSERT INTO pack_ocr_pages VALUES(?,?,?,?,?,?)",
                        (page_id, page_data.get("text", ""),
                         page_data.get("language"),
                         (page_data.get("confidence") or {}).get("mean"),
                         int(bool(page_data.get("review_required"))), raw["sha256"]),
                    )
                    count["ocr_pages"] += 1
                    # Enhanced OCR candidate evidence is stored independently
                    # from the original TSV. No candidate text becomes a
                    # validated technical fact merely because two modes agree.
                    variants, issues = _adaptive_ocr_evidence(page_data, n)
                    for ordinal, (region, method, bbox, candidate_text,
                                  crosschecked) in enumerate(variants):
                        db.execute(
                            "INSERT INTO pack_ocr_variants VALUES(?,?,?,?,?,?,?,0)",
                            (f"{page_id}:ocrvariant:{ordinal}", page_id,
                             region, method, json.dumps(bbox),
                             candidate_text, int(crosschecked)),
                        )
                        count["ocr_variants"] += 1
                    for ordinal, (region, method, bbox, reason_code) in enumerate(issues):
                        db.execute(
                            "INSERT INTO pack_ocr_issues VALUES(?,?,?,?,?,?,?)",
                            (f"{page_id}:ocrissue:{ordinal}", page_id, region,
                             method, json.dumps(bbox), reason_code, "unresolved"),
                        )
                        count["ocr_issues"] += 1
                    for ix, word in enumerate(page_data.get("words") or []):
                        db.execute(
                            "INSERT INTO pack_ocr_words VALUES(?,?,?,?,?,?,?,?)",
                            (page_id, ix, word.get("text", ""),
                             word.get("confidence"),
                             word.get("left"), word.get("top"),
                             word.get("width"), word.get("height")),
                        )
                        count["ocr_words"] += 1
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
        if count["ocr_pages"]:
            manifest["available_for"].append("direct_ocr_text_search_with_provenance")
            for member, content in sorted(ocr_members.items()):
                manifest["members_sha256"][member] = _hash(content)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise FileExistsError(f"Pack exists: {output_path}")
        entries = {
            "manifest.json": (json.dumps(
                manifest, sort_keys=True, ensure_ascii=False, indent=2
            ) + "\n").encode("utf-8"),
            "source/document.pdf": pdf_bytes,
            "data/knowledge.sqlite": db_bytes,
            **ocr_members,
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
            expected.update(manifest_name for manifest_name in archive.namelist()
                            if manifest_name.startswith("ocr/"))
            if set(archive.namelist()) != expected:
                return {"valid": False, "error": "unexpected_or_missing_members"}
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "mems-forge-document-pack":
                return {"valid": False, "error": "unknown_format"}
            if manifest.get("format_version") != 1:
                return {"valid": False, "error": "unknown_format_version"}
            if set(manifest["members_sha256"]) != expected - {"manifest.json"}:
                return {"valid": False, "error": "manifest_member_list_mismatch"}
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
                    ocr_pages = db.execute("SELECT COUNT(*) FROM pack_ocr_pages").fetchone()[0]
                    # Format-v1 packs issued before adaptive OCR had no
                    # variants table; they remain readable and unapproved.
                    table_present = db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='pack_ocr_variants'"
                    ).fetchone() is not None
                    ocr_variants = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_variants"
                    ).fetchone()[0] if table_present else 0)
                    issue_table_present = db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='pack_ocr_issues'"
                    ).fetchone() is not None
                    ocr_issues = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_issues"
                    ).fetchone()[0] if issue_table_present else 0)
                    incorrectly_approved = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_variants WHERE approved_for_ai<>0"
                    ).fetchone()[0] if table_present else 0)
                    metadata = db.execute(
                        "SELECT value FROM database_metadata WHERE key='publication_state'"
                    ).fetchone()
                    # Compare immutable OCR TSV with BOTH the SQLite words
                    # and the searchable text. Checksums alone are insufficient
                    # if one layer was generated from an unrelated OCR result.
                    for n in range(1, pages + 1):
                        page_id = "REV_" + manifest["source"]["sha256"] + f":p{n}"
                        indexed_page = db.execute(
                            "SELECT text,raw_tsv_sha256 FROM pack_ocr_pages "
                            "WHERE page_id=?", (page_id,),
                        ).fetchone()
                        if indexed_page is None:
                            continue
                        member = f"ocr/page-{n:04d}.tsv.gz"
                        if member not in archive.namelist():
                            return {"valid": False, "error": "ocr_source_tsv_missing", "page": n}
                        original_tsv = gzip.decompress(archive.read(member))
                        if _hash(original_tsv) != indexed_page[1]:
                            return {"valid": False, "error": "ocr_source_tsv_sha256_mismatch",
                                    "page": n}
                        parsed, warnings = _parse_words(
                            original_tsv.decode("utf-8"), n
                        )
                        if warnings:
                            return {"valid": False, "error": "ocr_source_tsv_parse_warning",
                                    "page": n}
                        if _words_to_text(parsed) != indexed_page[0]:
                            return {"valid": False, "error": "ocr_text_not_from_source_tsv",
                                    "page": n}
                        indexed_words = db.execute(
                            "SELECT text,confidence,left_px,top_px,width_px,height_px "
                            "FROM pack_ocr_words WHERE page_id=? ORDER BY word_index",
                            (page_id,),
                        ).fetchall()
                        expected_words = [
                            (word.text, word.confidence, word.left, word.top,
                             word.width, word.height) for word in parsed
                        ]
                        if indexed_words != expected_words:
                            return {"valid": False, "error": "ocr_words_not_from_source_tsv",
                                    "page": n}
                    if (integrity != "ok" or
                            db.execute("PRAGMA foreign_key_check").fetchall() or
                            pages != manifest["source"]["pages"] or
                            ocr_pages != manifest["counts"].get("ocr_pages", 0) or
                            ocr_variants != manifest["counts"].get("ocr_variants", 0) or
                            ocr_issues != manifest["counts"].get("ocr_issues", 0) or
                            incorrectly_approved or
                            metadata is None or metadata[0] != "blocked_semantic_review"):
                        return {"valid": False, "error": "sqlite_validation_failed"}
            return {
                "valid": True, "pack_id": manifest["pack_id"],
                "source_pages": manifest["source"]["pages"],
                "state": manifest["publication_state"],
            }
    except (OSError, ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
        return {"valid": False, "error": type(exc).__name__ + ": " + str(exc)}
