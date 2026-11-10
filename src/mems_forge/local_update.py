"""Offline installation and search for MEMS Forge document pack candidates.

This is a PREVIEW-only staging area. It never modifies MEMS Manager's
installed databases, releases, or AI. A separate approved publication gate
is required before a document becomes an official update.
"""
from __future__ import annotations

from contextlib import closing

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile

from .db_tester import search_database, search_installed_databases
from .document_pack import inspect_document_pack

_SHA_CHARS = frozenset("0123456789abcdef")
_MAX_PACK_MEMBER_BYTES = 2 * 1024 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def _require_sha256(value: object) -> str:
    text = str(value).lower()
    if len(text) != 64 or not set(text) <= _SHA_CHARS:
        raise ValueError("Invalid document SHA-256")
    return text


def _safe_entry(value: str) -> bool:
    parts = value.split("/")
    return (
        value in {"manifest.json", "source/document.pdf", "data/knowledge.sqlite"}
        or (len(parts) == 2 and parts[0] == "ocr"
            and parts[1].startswith("page-")
            and parts[1].endswith(".tsv.gz")
            and parts[1][5:-7].isdigit())
    )


def validate_installed_preview(path: Path) -> dict:
    """Re-read disk bytes; an existing directory isn't proof of correctness."""
    receipt_file = path / "installation.json"
    if not receipt_file.is_file():
        return {"valid": False, "error": "INSTALLATION_RECEIPT_MISSING"}
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if receipt["source_sha256"] != manifest["source"]["sha256"]:
            return {"valid": False, "error": "SOURCE_IDENTITY_MISMATCH"}
        if manifest["publication_state"] != "CANDIDATE_NOT_VALIDATED":
            return {"valid": False, "error": "UNEXPECTED_PUBLICATION_STATE"}
        members = manifest["members_sha256"]
        if not all(_safe_entry(name) for name in members):
            return {"valid": False, "error": "UNSAFE_MEMBER"}
        for filename, expected in members.items():
            file = path.joinpath(*filename.split("/"))
            if not file.is_file() or sha256_file(file) != expected:
                return {"valid": False, "error": "MEMBER_HASH_MISMATCH", "member": filename}
        with closing(sqlite3.connect(
            (path / "data" / "knowledge.sqlite").as_uri() + "?mode=ro",
            uri=True,
        )) as db:
            ok = db.execute("PRAGMA integrity_check").fetchone()[0]
            pages = db.execute("SELECT COUNT(*) FROM source_pages").fetchone()[0]
            ocr = db.execute("SELECT COUNT(*) FROM pack_ocr_pages").fetchone()[0]
            if ok != "ok" or pages != manifest["source"]["pages"]:
                return {"valid": False, "error": "SQLITE_PAGE_INTEGRITY_FAILED"}
            if ocr != manifest["counts"]["ocr_pages"]:
                return {"valid": False, "error": "SQLITE_OCR_COUNT_MISMATCH"}
        return {
            "valid": True, "source_sha256": receipt["source_sha256"],
            "pack_sha256": receipt["pack_sha256"],
            "pages": manifest["source"]["pages"],
            "ocr_pages": manifest["counts"]["ocr_pages"],
            "source_pdf": str(path / "source" / "document.pdf"),
            "state": "PREVIEW_UNVERIFIED_NOT_FOR_AI",
        }
    except (KeyError, ValueError, OSError, sqlite3.Error) as exc:
        return {"valid": False, "error": type(exc).__name__ + ": " + str(exc)}


def stage_document_pack(pack_path: Path, storage_root: Path) -> dict:
    """Verify, copy and atomically activate a candidate in a separate preview."""
    pack_path = Path(pack_path)
    storage_root = Path(storage_root)
    verified = inspect_document_pack(pack_path)
    if not verified["valid"]:
        raise ValueError("Rejected damaged or invalid source pack: " + str(verified))
    pack_sha = sha256_file(pack_path)
    with zipfile.ZipFile(pack_path, "r") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("publication_state") != "CANDIDATE_NOT_VALIDATED":
            raise ValueError("Preview installer accepts only unvalidated document candidates")
        doc_sha = _require_sha256(manifest["source"]["sha256"])
        members = set(archive.namelist())
        if any(not _safe_entry(name) for name in members):
            raise ValueError("Unexpected or unsafe archive member")
        destination = storage_root / "preview" / doc_sha
        if destination.exists():
            found = validate_installed_preview(destination)
            if not found["valid"]:
                raise ValueError("Existing staged source is corrupted: " + str(found))
            if found["pack_sha256"] != pack_sha:
                raise ValueError("Same source document has conflicting pack revisions")
            return {"action": "ALREADY_PRESENT", **found}
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".preview-", dir=destination.parent))
        try:
            for member in sorted(members):
                item = archive.getinfo(member)
                if item.file_size > _MAX_PACK_MEMBER_BYTES:
                    raise ValueError("Member exceeds maximum supported size")
                target = staging.joinpath(*member.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                if member != "manifest.json":
                    expected = manifest["members_sha256"][member]
                    if sha256_file(target) != expected:
                        raise ValueError(f"Installed file hash mismatch: {member}")
            (staging / "installation.json").write_text(
                json.dumps({
                    "schema": 1, "source_sha256": doc_sha,
                    "pack_sha256": pack_sha, "scope": "preview_unverified",
                    "approved_for_ai": False,
                    "app_databases_modified": False,
                }, sort_keys=True, indent=2) + "\n", encoding="utf-8",
            )
            check = validate_installed_preview(staging)
            if not check["valid"]:
                raise ValueError("Invalid staged update: " + str(check))
            # Publication is a single directory rename. Failed staging never
            # destroys prior data or touches an installed application database.
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {"action": "INSTALLED_PREVIEW", **validate_installed_preview(destination)}


def list_staged(storage_root: Path) -> list[dict]:
    directory = Path(storage_root) / "preview"
    if not directory.is_dir():
        return []
    return [
        validate_installed_preview(path)
        for path in sorted(directory.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]


def search_staged(storage_root: Path, query: str, limit: int = 20) -> dict:
    """Return page-aware raw/OCR evidence, never approved technical claims."""
    results = []
    errors = []
    for installed in list_staged(storage_root):
        if not installed.get("valid"):
            errors.append(installed)
            continue
        index = Path(storage_root) / "preview" / installed["source_sha256"]
        answer = search_database(index / "data" / "knowledge.sqlite", query, limit=limit)
        if answer["errors"]:
            errors.extend(answer["errors"])
            continue
        for item in answer["results"]:
            if len(results) >= limit:
                break
            results.append({
                "source_document_sha256": installed["source_sha256"],
                "source_pdf": installed["source_pdf"],
                "source_table": item["source_table"],
                "fields": item["fields"],
                "validation": "UNVERIFIED_SOURCE_TEXT",
                "approved_for_ai": False,
            })
    return {"query": query, "count": len(results), "results": results, "errors": errors}


def search_all_sources(
    install_root: Path, storage_root: Path, query: str, limit: int = 20,
) -> dict:
    """Read both immutable application references and added raw PDF previews.

    New-source matches stay labelled UNVERIFIED and cannot become automatic
    AI answers merely because the query matched a source page.
    """
    limit = max(1, min(50, limit))
    existing = search_installed_databases(install_root, query, limit=limit)
    preview = search_staged(storage_root, query, limit=limit)
    old_rows = [
        {"scope": "EXISTING_REFERENCE", "validation": "INSTALLED_REFERENCE",
         "source_database": row["source_database"], "fields": row["fields"],
         "source_table": row["source_table"],
         "approved_for_ai": None}
        for row in existing["results"]
    ]
    new_rows = [
        {"scope": "DOCUMENT_UPDATE_PREVIEW", **row}
        for row in preview["results"]
    ]
    result_rows = []
    # Reserve visibility for both sources when a shared query matches both.
    if old_rows and new_rows and limit >= 2:
        result_rows.extend([old_rows.pop(0), new_rows.pop(0)])
    while len(result_rows) < limit and (old_rows or new_rows):
        if old_rows:
            result_rows.append(old_rows.pop(0))
        if new_rows and len(result_rows) < limit:
            result_rows.append(new_rows.pop(0))
    return {
        "query": query,
        "count": len(result_rows),
        "results": result_rows,
        "existing_matches": existing["count"],
        "preview_matches": preview["count"],
        "errors": existing["errors"] + preview["errors"],
        "updates_are_approved": False,
        "application_databases_modified": False,
    }
