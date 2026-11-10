"""Reconcile every indexed PDF page against its immutable original source.

This audit can certify byte integrity and extraction consistency; it cannot
certify the technical meaning, model applicability, tables or diagrams. Never
equate a clean mechanical audit with permission to publish or feed the AI.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import zipfile

import pymupdf

from .document_pack import inspect_document_pack
from .pdf_reader import _iter_text_records

_TECH_TOKEN = re.compile(r"(?<![\w])[A-Za-z]*\d+[A-Za-z0-9.,/-]*(?![\w])")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _technical_tokens(text: str) -> Counter:
    # Multiplicity matters: a repeated setting or ECU identifier can be lost.
    return Counter(token.casefold() for token in _TECH_TOKEN.findall(text))


def _issue(code: str, page: int | None = None, **details: object) -> dict:
    issue = {"code": code}
    if page is not None:
        issue["page_number"] = page
    issue.update(details)
    return issue


def audit_source_pack(pack_path: Path) -> dict:
    """Check raw block/span parity and OCR risks page by page.

    No file in the pack or existing ECU MEMS database is modified. The PDF
    itself is independently reopened and compared to the indexed raw text.
    Candidate publication remains blocked even if all mechanical tests pass.
    """
    pack_path = Path(pack_path)
    verdict = inspect_document_pack(pack_path)
    report = {
        "audit_version": 1,
        "pack": str(pack_path),
        "pack_sha256": None,
        "pack_integrity_ok": bool(verdict.get("valid")),
        "native_source_parity_ok": False,
        "source_pages": 0,
        "pages_checked": 0,
        "native_text_pages": 0,
        "ocr_pages": 0,
        "issues": [],
        "reviews": [],
        "publication_state": "BLOCKED_SEMANTIC_VALIDATION",
        "approved_for_ai": False,
        "approved_for_release": False,
    }
    if not verdict.get("valid"):
        report["issues"].append(_issue("INVALID_PACK", error=verdict.get("error")))
        return report

    report["pack_sha256"] = _sha256_file(pack_path)
    try:
        with zipfile.ZipFile(pack_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            report["source_pages"] = int(manifest["source"]["pages"])
            with tempfile.TemporaryDirectory(prefix="mems-forge-source-audit-") as root:
                original = Path(root) / "document.pdf"
                index = Path(root) / "knowledge.sqlite"
                original.write_bytes(archive.read("source/document.pdf"))
                index.write_bytes(archive.read("data/knowledge.sqlite"))
                with pymupdf.open(original) as pdf, closing(
                    sqlite3.connect("file:" + index.as_posix() + "?mode=ro", uri=True)
                ) as db:
                    if pdf.page_count != report["source_pages"]:
                        report["issues"].append(_issue(
                            "PDF_PAGE_COUNT_MISMATCH", expected=report["source_pages"],
                            actual=pdf.page_count,
                        ))
                        return report
                    db.row_factory = sqlite3.Row
                    stored_pages = db.execute(
                        "SELECT physical_page_number,page_id,content_kind "
                        "FROM source_pages ORDER BY physical_page_number"
                    ).fetchall()
                    if [row["physical_page_number"] for row in stored_pages] != list(
                        range(1, pdf.page_count + 1)
                    ):
                        report["issues"].append(_issue("INDEX_PAGE_SEQUENCE_INVALID"))
                        return report
                    source_sha = manifest["source"]["sha256"]
                    revision = "REV_" + source_sha
                    revisions = db.execute(
                        "SELECT revision_id,sha256,page_count FROM document_revisions"
                    ).fetchall()
                    if (len(revisions) != 1 or revisions[0]["revision_id"] != revision
                            or revisions[0]["sha256"] != source_sha
                            or revisions[0]["page_count"] != pdf.page_count):
                        report["issues"].append(_issue("REVISION_PROVENANCE_MISMATCH"))
                        return report
                    for number, page in enumerate(pdf, start=1):
                        entry = stored_pages[number - 1]
                        page_id = entry["page_id"]
                        if page_id != f"{revision}:p{number}":
                            report["issues"].append(_issue("PAGE_ID_MISMATCH", number))
                            continue
                        expected_blocks, expected_spans, _ = _iter_text_records(
                            page.get_text("dict", sort=False), source_sha, number
                        )
                        actual_blocks = db.execute(
                            "SELECT geometric_order,source_text FROM raw_blocks "
                            "WHERE page_id=? ORDER BY geometric_order", (page_id,)
                        ).fetchall()
                        if len(actual_blocks) != len(expected_blocks) or any(
                            row["geometric_order"] != expected["block_index"] or
                            (row["source_text"] or "") != (expected.get("text") or "")
                            for row, expected in zip(actual_blocks, expected_blocks)
                        ):
                            report["issues"].append(_issue(
                                "NATIVE_BLOCK_PARITY_FAILED", number,
                                expected=len(expected_blocks), actual=len(actual_blocks),
                            ))

                        actual_spans = dict(db.execute(
                            "SELECT raw_span_id,source_text FROM raw_spans "
                            "WHERE raw_block_id IN "
                            "(SELECT raw_block_id FROM raw_blocks WHERE page_id=?)",
                            (page_id,),
                        ).fetchall())
                        expected_spans_by_id = {
                            f"{page_id}:b{s['block_index']}:l{s['line_index']}:s{s['span_index']}":
                            s["text"] for s in expected_spans
                        }
                        if actual_spans != expected_spans_by_id:
                            report["issues"].append(_issue(
                                "NATIVE_SPAN_PARITY_FAILED", number,
                                expected=len(expected_spans_by_id), actual=len(actual_spans),
                            ))
                        native_text = "\n".join(
                            item.get("text", "") for item in expected_blocks
                        ).strip()
                        if native_text:
                            report["native_text_pages"] += 1

                        ocr = db.execute(
                            "SELECT text,review_required,confidence_mean "
                            "FROM pack_ocr_pages WHERE page_id=?", (page_id,)
                        ).fetchone()
                        if ocr is not None:
                            report["ocr_pages"] += 1
                            if ocr["review_required"]:
                                report["reviews"].append(_issue("OCR_ENGINE_REVIEW", number))
                            if ocr["confidence_mean"] is None or ocr["confidence_mean"] < 85:
                                report["reviews"].append(_issue(
                                    "OCR_CONFIDENCE_LOW_OR_UNKNOWN", number,
                                    confidence=ocr["confidence_mean"],
                                ))
                            if native_text:
                                missing = _technical_tokens(native_text) - _technical_tokens(
                                    ocr["text"] or ""
                                )
                                if missing:
                                    report["reviews"].append(_issue(
                                        "OCR_TECHNICAL_TOKENS_DIFFER_FROM_NATIVE", number,
                                        examples=list(missing.elements())[:12],
                                    ))
                            else:
                                report["reviews"].append(_issue(
                                    "OCR_ONLY_REQUIRES_VISUAL_VERIFICATION", number
                                ))
                        elif not native_text:
                            report["issues"].append(_issue(
                                "NO_NATIVE_TEXT_OR_OCR", number
                            ))
                        if any(int(b["block_type"]) != 0 for b in expected_blocks):
                            report["reviews"].append(_issue(
                                "IMAGE_OR_NON_TEXT_BLOCK_REQUIRES_VISUAL_REVIEW", number
                            ))
                        # PDF vector drawings can be wiring diagrams without any raster image.
                        # Their meaning cannot be established by text/OCR matching.
                        if page.get_drawings():
                            report["reviews"].append(_issue(
                                "VECTOR_DRAWINGS_REQUIRE_VISUAL_REVIEW", number
                            ))
                        visual_count = db.execute(
                            "SELECT COUNT(*) FROM pack_image_occurrences "
                            "WHERE revision_id=? AND page_number=?",
                            (revision, number),
                        ).fetchone()[0]
                        if visual_count:
                            report["reviews"].append(_issue(
                                "GRAPHIC_OCCURRENCES_REQUIRE_REVIEW", number,
                                count=visual_count,
                            ))
                        report["pages_checked"] += 1
                    if db.execute("PRAGMA foreign_key_check").fetchall():
                        report["issues"].append(_issue("FOREIGN_KEY_CHECK_FAILED"))
    except (OSError, ValueError, KeyError, sqlite3.Error, RuntimeError, zipfile.BadZipFile) as exc:
        report["issues"].append(_issue(
            "AUDIT_FAILED_CLOSED", error=f"{type(exc).__name__}: {exc}"
        ))
    report["native_source_parity_ok"] = (
        not report["issues"] and report["pages_checked"] == report["source_pages"]
    )
    return report


def write_source_audit(pack_path: Path, destination: Path) -> dict:
    """Persist an honest machine audit; a JSON report is not a release permit."""
    report = audit_source_pack(pack_path)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return report
