"""Audit the *installed* MEMS Manager package against the original RAVE PDFs.

This is a read-only, independently run verifier. It never writes to the
application's SQLite files, never silently accepts a missing input, and never
equates an image filename with a readable image. All findings have provenance.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
IMPORTANT_TABLES = (
    "memslibrary_search",
    "ravemems_document",
    "ravemems_page",
    "mems_knowledge_item",
    "search_documents",
    "structured_entities",
    "provenance",
    "assets",
    "review_items",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1()
    digest.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", Path(str(value)).stem.casefold())


def _safe_file(root: Path, relative: str) -> Path | None:
    """Block absolute paths, traversal and symlinks outside the install root."""
    text = str(relative).replace("\\", "/")
    parts = text.split("/")
    if not text or text.startswith("/") or ":" in text or any(
        part in {"", ".", ".."} for part in parts
    ):
        return None
    base = root.resolve()
    path = (base / text).resolve()
    if not path.is_relative_to(base):
        return None
    return path


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    # URI mode=ro guarantees the audit cannot modify an installed database.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def inspect_sqlite(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path), "exists": path.is_file(), "status": "MISSING",
        "tables": {}, "document_keys": [], "pages_by_document": {},
        "errors": [],
    }
    if not path.is_file():
        return info
    try:
        with _readonly_sqlite(path) as database:
            status = database.execute("PRAGMA quick_check").fetchone()
            info["integrity"] = status[0] if status else "NO_RESULT"
            errors = database.execute("PRAGMA foreign_key_check").fetchmany(20)
            info["foreign_key_violations_first_20"] = len(errors)
            info["status"] = "OK" if info["integrity"] == "ok" and not errors else "INVALID"
            names = {
                row[0] for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in IMPORTANT_TABLES:
                if table in names:
                    # Table names come from a fixed trusted constant.
                    info["tables"][table] = database.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
            # The revision table can preserve the PDF's original SHA-256
            # even when its user-facing document_key is an RCL catalogue ID.
            # Inspect candidates explicitly rather than guessing identities.
            info["revision_schemas"] = {}
            info["revision_metadata"] = []
            for table in sorted(names):
                if not table.startswith("ravemems_") or not any(
                    token in table for token in ("revision", "source", "file")
                ):
                    continue
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                    continue
                columns = [
                    row[1] for row in database.execute(f'PRAGMA table_info("{table}")')
                ]
                info["revision_schemas"][table] = columns
                for row in database.execute(f'SELECT * FROM "{table}" LIMIT 150'):
                    info["revision_metadata"].append({
                        "table": table,
                        **{
                            field: (value[:500] if isinstance(value, str) else
                                    value if isinstance(value, (int, float, type(None)))
                                    else str(value)[:500])
                            for field, value in dict(row).items()
                        },
                    })
            if "ravemems_document" in names:
                # Document IDs can be SHA-256-derived, not PDF filenames.
                # Preserve the pack's own metadata so each alternate name
                # can be checked against source fingerprints/provenance.
                info["document_table_columns"] = [
                    row[1] for row in database.execute(
                        "PRAGMA table_info(ravemems_document)"
                    )
                ]
                info["document_metadata"] = [
                    {
                        field: (value[:500] if isinstance(value, str) else
                                value if isinstance(value, (int, float, type(None)))
                                else str(value)[:500])
                        for field, value in dict(row).items()
                    }
                    for row in database.execute("SELECT * FROM ravemems_document LIMIT 200")
                ]
            if "ravemems_page" in names:
                info["page_table_columns"] = [
                    row[1] for row in database.execute("PRAGMA table_info(ravemems_page)")
                ]
            if "memslibrary_search" in names:
                columns = {
                    row[1] for row in database.execute(
                        "PRAGMA table_info(memslibrary_search)"
                    )
                }
                if "document_key" in columns:
                    info["document_keys"] = [
                        str(row[0]) for row in database.execute(
                            "SELECT DISTINCT document_key FROM memslibrary_search "
                            "WHERE document_key IS NOT NULL ORDER BY document_key"
                        )
                    ]
                    if "page_number" in columns:
                        info["pages_by_document"] = {
                            str(row[0]): int(row[1])
                            for row in database.execute(
                                "SELECT document_key, COUNT(DISTINCT page_number) "
                                "FROM memslibrary_search WHERE page_number IS NOT NULL "
                                "GROUP BY document_key"
                            )
                        }
            if "ravemems_page" in names:
                columns = {
                    row[1] for row in database.execute("PRAGMA table_info(ravemems_page)")
                }
                page_col = next(
                    (c for c in ("physical_page", "page_number") if c in columns), None
                )
                if page_col and "document_key" in columns:
                    info["source_pages_by_document"] = {
                        str(row[0]): int(row[1])
                        for row in database.execute(
                            f'SELECT document_key, COUNT(DISTINCT "{page_col}") '
                            'FROM ravemems_page GROUP BY document_key'
                        )
                    }
            if "review_items" in names:
                cols = {row[1] for row in database.execute("PRAGMA table_info(review_items)")}
                if "status" in cols:
                    info["pending_reviews"] = database.execute(
                        "SELECT COUNT(*) FROM review_items WHERE status='pending'"
                    ).fetchone()[0]
    except (sqlite3.Error, OSError) as exc:
        info["status"] = "INVALID"
        info["errors"].append(str(exc))
    return info


def inspect_pdf(path: Path) -> tuple[int | None, str | None]:
    try:
        import pymupdf
        with pymupdf.open(path) as document:
            if document.page_count < 1:
                return None, "PDF sans pages"
            return document.page_count, None
    except Exception as exc:
        return None, f"Lecture PDF impossible: {exc}"


def audit_originals(source_repo: Path, baseline: dict[str, Any]) -> dict[str, Any]:
    entries = baseline.get("documents", [])
    records: list[dict[str, Any]] = []
    issues: list[str] = []
    names: set[str] = set()
    expected_paths: set[str] = set()
    for item in entries:
        relative = item["path"]
        if relative in expected_paths:
            issues.append(f"MANIFEST_DUPLICATE:{relative}")
            continue
        expected_paths.add(relative)
        path = _safe_file(source_repo, relative)
        record: dict[str, Any] = {
            "path": relative, "status": "MISSING", "pages": None,
            "expected_size": item.get("size_bytes"),
            "expected_git_blob_sha1": item.get("git_blob_sha1"),
        }
        if path is not None and path.is_file():
            size = path.stat().st_size
            original_sha = git_blob_sha1(path)
            record.update(size_bytes=size, git_blob_sha1=original_sha,
                          sha256=file_sha256(path))
            pages, error = inspect_pdf(path)
            record["pages"] = pages
            if error:
                record["error"] = error
            valid = (size == item.get("size_bytes")
                     and original_sha == item.get("git_blob_sha1")
                     and pages is not None)
            record["status"] = "OK" if valid else "INVALID"
        if record["status"] != "OK":
            issues.append(f"ORIGINAL_{record['status']}:{relative}")
        records.append(record)
        names.add(normalized_key(Path(relative).stem))
    source_pdf_dir = source_repo / "rave"
    additional = []
    if source_pdf_dir.is_dir():
        for path in sorted(source_pdf_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".pdf":
                relative = path.relative_to(source_repo).as_posix()
                if relative not in expected_paths:
                    additional.append(relative)
    if additional:
        issues.extend(f"ORIGINAL_NOT_IN_MANIFEST:{name}" for name in additional)
    return {
        "expected": len(entries), "ok": sum(d["status"] == "OK" for d in records),
        "records": records, "extra_pdf_paths": additional, "issues": issues,
        "total_pages": sum(d["pages"] or 0 for d in records),
    }


def audit_reference(install_root: Path) -> dict[str, Any]:
    root = install_root / "database" / "reference"
    manifest_file = root / "manifest.json"
    info: dict[str, Any] = {
        "manifest": str(manifest_file), "manifest_present": manifest_file.is_file(),
        "expected_images": 0, "available_images": 0, "sha256_verified": 0,
        "missing": [], "hash_mismatch": [], "unsafe_paths": [],
        "unlisted_images": [], "missing_enrichments": [], "seed_count": 0,
    }
    if not manifest_file.is_file():
        return info
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        info["error"] = str(exc)
        return info
    names: dict[str, str | None] = {}
    for label, item in manifest.get("visual_assets", {}).items():
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            name = item["path"]
            names.setdefault(name, item.get("sha256"))
    for label, entry in manifest.get("diagrams", {}).items():
        if isinstance(entry, str):
            names.setdefault(entry, None)
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            names.setdefault(entry["path"], entry.get("sha256"))
    info["expected_images"] = len(names)
    info["manifest_revision"] = manifest.get("database_revision")
    for relative, digest in sorted(names.items()):
        file = _safe_file(root, relative)
        if file is None or file.suffix.lower() not in IMAGE_SUFFIXES:
            info["unsafe_paths"].append(relative)
        elif not file.is_file():
            info["missing"].append(relative)
        else:
            info["available_images"] += 1
            if digest:
                if file_sha256(file).lower() == str(digest).lower():
                    info["sha256_verified"] += 1
                else:
                    info["hash_mismatch"].append(relative)
    image_dir = root / "images"
    if image_dir.is_dir():
        for image in image_dir.rglob("*"):
            if image.is_file() and image.suffix.lower() in IMAGE_SUFFIXES:
                relative = image.relative_to(root).as_posix()
                if relative not in names:
                    info["unlisted_images"].append(relative)
    enrichments = manifest.get("research_enrichment_batches", [])
    for relative in enrichments:
        file = _safe_file(root, str(relative))
        if file is None or not file.is_file():
            info["missing_enrichments"].append(str(relative))
    info["expected_enrichments"] = len(enrichments)
    info["seed_count"] = len(list(root.glob("mems_reference_seed_*.qz64")))
    return info


def resolve_image_reference(
    install_root: Path, reference: str, *, manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Resolve either a source-relative path or a symbolic RAVE image key.

    The expert SQLite often stores 'RCL0194ENG:20.3'; the manifest maps
    'rave:RCL0194ENG:20.3' to 'images/rave/RCL0194ENG_20_3.png'.
    Never invent a path or classify a mere text key as a real image.
    """
    root = install_root / "database" / "reference"
    value = reference.strip()
    outcome: dict[str, Any] = {
        "reference": value, "status": "NOT_LISTED", "path": None,
    }
    if not value:
        outcome["status"] = "EMPTY_REFERENCE"
        return outcome
    if manifest is None:
        path = root / "manifest.json"
        if not path.is_file():
            outcome["status"] = "MANIFEST_MISSING"
            return outcome
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            outcome["status"] = "MANIFEST_INVALID"
            return outcome

    relative = value.replace("\\", "/")
    expected_sha: str | None = None
    if relative.lower().startswith("database/reference/"):
        relative = relative[len("database/reference/"):]
    if not relative.lower().startswith("images/"):
        visual = manifest.get("visual_assets", {})
        diagrams = manifest.get("diagrams", {})
        item = visual.get(value) or visual.get("rave:" + value)
        if item is None:
            item = diagrams.get(value)
        if isinstance(item, dict):
            relative = str(item.get("path", ""))
            expected_sha = item.get("sha256")
        elif isinstance(item, str):
            relative = item
        else:
            return outcome
    image = _safe_file(root, relative)
    if image is None or image.suffix.lower() not in IMAGE_SUFFIXES:
        outcome["status"] = "UNSAFE_PATH"
    elif not image.is_file():
        outcome["status"] = "FILE_MISSING"
        outcome["relative_path"] = relative
    elif expected_sha and file_sha256(image).lower() != str(expected_sha).lower():
        outcome["status"] = "HASH_MISMATCH"
        outcome["relative_path"] = relative
    else:
        outcome["status"] = "OK"
        outcome["path"] = str(image)
        outcome["relative_path"] = relative
        outcome["sha256_verified"] = bool(expected_sha)
    return outcome


def expand_image_references(value: str) -> list[str]:
    """Expand explicit comma-separated diagram ids and short numeric ranges.

    Examples:
      RCL0194ENG:20.2,39.1 -> RCL0194ENG:20.2 / RCL0194ENG:39.1
      RCL0194ENG:20.1-20.2 -> RCL0194ENG:20.1 / RCL0194ENG:20.2
    Do not guess across section boundaries, unknown syntaxes or long ranges.
    """
    text = value.strip()
    parsed = re.fullmatch(
        r"([A-Za-z0-9_]+):(\d+\.\d+(?:\s*[,;-]\s*\d+\.\d+)*)",
        text,
    )
    if not parsed:
        return [text] if text else []
    document, expression = parsed.groups()
    results: list[str] = []
    parts = re.split(r"\s*[,;]\s*", expression)
    for part in parts:
        interval = re.fullmatch(r"(\d+)\.(\d+)-(\d+)\.(\d+)", part)
        if interval:
            start_major, first, end_major, last = map(int, interval.groups())
            if start_major == end_major and 0 <= last-first <= 12:
                results.extend(
                    f"{document}:{start_major}.{n}"
                    for n in range(first, last+1)
                )
            else:
                results.append(f"{document}:{part}")
        else:
            results.append(f"{document}:{part}")
    return list(dict.fromkeys(results))


def resolve_image_references(
    install_root: Path, expression: str,
    *, manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        resolve_image_reference(install_root, ref, manifest=manifest)
        for ref in expand_image_references(expression)
    ]


def audit_expert_image_links(install_root: Path, expert_path: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {
        "total": 0, "verified": 0, "unresolved": [],
        "status_counts": {}, "source": str(expert_path),
        "resolved_image_parts": 0, "missing_reference_parts": [],
    }
    if not expert_path.is_file():
        return findings
    manifest_file = install_root / "database" / "reference" / "manifest.json"
    if not manifest_file.is_file():
        return findings
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        with _readonly_sqlite(expert_path) as db:
            cols = {row[1] for row in db.execute("PRAGMA table_info(mems_knowledge_item)")}
            if not {"image_ref", "knowledge_key"} <= cols:
                return findings
            references = db.execute(
                "SELECT knowledge_key,image_ref FROM mems_knowledge_item "
                "WHERE image_ref IS NOT NULL AND trim(image_ref) != ''"
            )
            cache: dict[str, dict[str, Any]] = {}
            for key, ref in references:
                ref = str(ref)
                if ref not in cache:
                    cache[ref] = resolve_image_references(
                        install_root, ref, manifest=manifest,
                    )
                resolved = cache[ref]
                found = [link for link in resolved if link["status"] == "OK"]
                missing = [link for link in resolved if link["status"] != "OK"]
                findings["total"] += 1
                findings["resolved_image_parts"] += len(found)
                status = ("OK" if not missing else
                          "PARTIAL" if found else "NOT_RESOLVED")
                findings["status_counts"][status] = (
                    findings["status_counts"].get(status, 0) + 1
                )
                if status == "OK":
                    findings["verified"] += 1
                else:
                    findings["unresolved"].append({
                        "knowledge_key": key, "image_ref": ref,
                        "status": status,
                        "missing_parts": [x["reference"] for x in missing],
                        "available_parts": [x["reference"] for x in found],
                    })
                    findings["missing_reference_parts"].extend(
                        x["reference"] for x in missing
                    )
    except (ValueError, OSError, sqlite3.Error) as exc:
        findings["error"] = str(exc)
    return findings


def _source_to_pack_key(source: dict[str, Any], pack: dict[str, Any]) -> tuple[str | None, str]:
    """Only explicit names or independently verifiable SHA-256 identities.

    In the real Pack001, many keys use DOC_PDFSHA256 + 16 hexadecimal digits.
    Compare to SHA-256 of the *actual* PDF. Never equate documents based on
    page counts or similar titles.
    """
    sha = str(source.get("sha256", "")).upper()
    stem = normalized_key(Path(source.get("path", source.get("file", ""))).stem)
    possible: dict[str, str] = {}
    for key in pack.get("document_keys", []):
        upper = key.upper()
        if upper.startswith("DOC_PDFSHA256") and len(upper) > len("DOC_PDFSHA256"):
            prefix = upper[len("DOC_PDFSHA256"):]
            if (len(prefix) >= 16 and all(c in "0123456789ABCDEF" for c in prefix)
                    and sha.startswith(prefix)):
                possible[key] = "SHA256_PREFIX_OF_ORIGINAL_PDF"
        if normalized_key(key) == stem:
            possible[key] = "EXACT_DOCUMENT_NAME"

    for metadata in (
        pack.get("document_metadata", []) + pack.get("revision_metadata", [])
    ):
        key = metadata.get("document_key")
        if key not in pack.get("document_keys", []):
            continue
        # Only metadata fields declaring hash identity, not arbitrary text.
        for field, value in metadata.items():
            lower = field.lower()
            if "sha256" not in lower and "hash" not in lower:
                continue
            literal = str(value or "").strip().upper()
            if len(literal) == 64 and literal == sha:
                possible[key] = "DOCUMENT_METADATA_EXACT_SHA256"
            elif len(literal) >= 16 and sha.startswith(literal):
                possible[key] = "DOCUMENT_METADATA_SHA256_PREFIX"
        for field, value in metadata.items():
            if field.lower() in {"source_filename", "pdf_filename", "source_file"}:
                if normalized_key(str(value)) == stem:
                    possible.setdefault(key, "DOCUMENT_METADATA_ORIGINAL_FILENAME")

    if len(possible) != 1:
        return None, "UNMATCHED" if not possible else "AMBIGUOUS"
    return next(iter(possible.items()))


def _pack_comparison(originals: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    pack_names = pack.get("document_keys", [])
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    used: set[str] = set()
    for row in originals["records"]:
        key, method = _source_to_pack_key(row, pack)
        if key and key not in used:
            used.add(key)
            indexed_pages = pack.get("pages_by_document", {}).get(key)
            source_pages = pack.get("source_pages_by_document", {}).get(key)
            matched.append({
                "pdf": row["path"], "document_key": key,
                "proof": method, "original_pages": row["pages"],
                "pages_in_search_index": indexed_pages,
                "pages_in_source_table": source_pages,
                "source_page_coverage": "UNVERIFIED" if source_pages is None or row["pages"] is None
                    else "COMPLETE_COUNT" if source_pages == row["pages"] else "COUNT_MISMATCH",
            })
        else:
            unmatched.append({
                "pdf": row["path"], "original_pages": row["pages"],
                "reason": "source_id_not_proven" if not key else "ambiguous_source_id",
            })
    return {
        "pack_document_keys": pack_names, "count_in_pack": len(pack_names),
        "matched": matched, "unmatched": unmatched,
        "unmatched_count": len(unmatched),
        "pack_keys_without_source_identity": sorted(set(pack_names) - used),
        "verified_original_pages": sum(x.get("original_pages") or 0 for x in matched),
        "original_pages_without_identity": sum(x.get("original_pages") or 0 for x in unmatched),
        "source_total_pages": originals["total_pages"],
        "pack_total_page_rows": pack.get("tables", {}).get("ravemems_page", 0),
        "notes": "Verified by source PDF SHA-256 or explicit metadata identity. "
                 "Matching total document/page counts alone never proves provenance.",
    }


def audit_install(
    source_repo: Path, install_root: Path, baseline_path: Path,
    *, extra_pdf_root: Path | None = None,
) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    original = audit_originals(source_repo, baseline)
    package = audit_reference(install_root)
    locations = {
        "memslibrary_pack": install_root / "MEMSLibrary_Pack_001" / "knowledge.sqlite",
        "expert_database": install_root / "database" / "expert" / "ia_mems_reference_r20.sqlite",
    }
    databases = {key: inspect_sqlite(path) for key, path in locations.items()}
    comparison = _pack_comparison(original, databases["memslibrary_pack"])
    expert_image_links = audit_expert_image_links(
        install_root, locations["expert_database"]
    )

    extra_pdfs: list[dict[str, Any]] = []
    if extra_pdf_root is not None:
        if extra_pdf_root.is_dir():
            for file in sorted(extra_pdf_root.rglob("*")):
                if file.is_file() and file.suffix.lower() == ".pdf":
                    pages, error = inspect_pdf(file)
                    extra_pdfs.append({
                        "file": str(file), "pages": pages,
                        "sha256": file_sha256(file), "error": error,
                    })
        else:
            extra_pdfs.append({"file": str(extra_pdf_root), "error": "INPUT_ABSENT"})

    # Extra PDF inputs are inventoried separately. They must not be silently
    # treated as part of the original 47 or as published pack material.
    for extra in extra_pdfs:
        key, proof = _source_to_pack_key(extra, databases["memslibrary_pack"])
        extra["document_key"] = key
        extra["mapping_proof"] = proof

    blockers: list[str] = []
    blockers.extend(original["issues"])
    if original["expected"] < 1:
        blockers.append("ORIGINAL_MANIFEST_EMPTY")
    if comparison["unmatched"]:
        blockers.append(f"PDF_NOT_MAPPED_TO_PACK:{len(comparison['unmatched'])}")
    if not package["manifest_present"]:
        blockers.append("REFERENCE_MANIFEST_MISSING")
    if package.get("error"):
        blockers.append("REFERENCE_MANIFEST_INVALID")
    if package["expected_images"] == 0:
        blockers.append("REFERENCE_IMAGE_INVENTORY_EMPTY")
    if package["missing"]:
        blockers.append(f"IMAGES_MISSING:{len(package['missing'])}")
    if package["hash_mismatch"]:
        blockers.append(f"IMAGES_HASH_MISMATCH:{len(package['hash_mismatch'])}")
    if package["unsafe_paths"]:
        blockers.append(f"UNSAFE_IMAGE_PATHS:{len(package['unsafe_paths'])}")
    if package["missing_enrichments"]:
        blockers.append(f"ENRICHMENT_FILES_MISSING:{len(package['missing_enrichments'])}")
    if package["seed_count"] < 1:
        blockers.append("REFERENCE_SEED_FILES_MISSING")
    for name, database in databases.items():
        if database["status"] != "OK":
            blockers.append(f"SQLITE_{name}_{database['status']}")
    if databases["memslibrary_pack"]["tables"].get("memslibrary_search", 0) == 0:
        blockers.append("PACK_SEARCH_EMPTY")
    if expert_image_links.get("error"):
        blockers.append("EXPERT_IMAGE_LINK_AUDIT_FAILED")
    if expert_image_links["unresolved"]:
        blockers.append(
            "EXPERT_IMAGE_REFERENCES_NOT_OPENABLE:" +
            str(len(expert_image_links["unresolved"]))
        )
    if any(item.get("error") for item in extra_pdfs):
        blockers.append("ADDITIONAL_PDF_INPUT_UNREADABLE")
    if extra_pdfs and any(not item.get("document_key") for item in extra_pdfs):
        blockers.append(
            "ADDITIONAL_PDFS_NOT_MAPPED_TO_PACK:" +
            str(sum(not item.get("document_key") for item in extra_pdfs))
        )

    unverified = []
    if comparison["matched"] and any(
        x["source_page_coverage"] != "COMPLETE_COUNT" for x in comparison["matched"]
    ):
        unverified.append("SOURCE_PAGE_COVERAGE_NOT_PROVEN")
    # A clean file inventory is NOT a semantic review or a proof that each
    # original table, paragraph and figure was extracted correctly.
    unverified.append("SEMANTIC_COVERAGE_REQUIRES_PAGE_LEVEL_AUDIT")
    if extra_pdf_root is None:
        unverified.append("OTHER_USER_SUPPLIED_PDFS_NOT_IN_THIS_BASELINE")
    state = "BLOCKED" if blockers else "REVIEW_REQUIRED" if unverified else "TECHNICALLY_VERIFIED"
    return {
        "schema": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": baseline.get("origin", {}),
        "install_root": str(install_root),
        "source_documents": original,
        "other_pdfs": extra_pdfs,
        "reference_images_and_enrichments": package,
        "databases": databases,
        "document_mapping": comparison,
        "knowledge_image_references": expert_image_links,
        "publication_gate": {
            "state": state, "blockers": sorted(set(blockers)),
            "not_yet_proven": unverified,
            "safe_to_claim_all_originals_exploitable": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    o = report["source_documents"]
    img = report["reference_images_and_enrichments"]
    mapping = report["document_mapping"]
    image_links = report["knowledge_image_references"]
    gate = report["publication_gate"]
    lines = [
        "# MEMS Forge — Audit de la documentation réellement installée",
        "",
        f"État : **{gate['state']}**",
        f"PDF originaux vérifiés : **{o['ok']}/{o['expected']}**",
        f"Pages des PDF vérifiés : **{o['total_pages']}**",
        f"Documents reliés au Pack001 : **{len(mapping['matched'])}/{o['expected']}**",
        f"Documents non reliés : **{mapping['unmatched_count']}**",
        f"Pages originales sans correspondance prouvée : **{mapping['original_pages_without_identity']}**",
        f"Pages présentes dans la table du pack : **{mapping['pack_total_page_rows']}** "
        "(égalité de compte ≠ preuve d'identité)",
        f"Images installées : **{img['available_images']}/{img['expected_images']}**",
        f"Images contrôlées par SHA-256 : **{img['sha256_verified']}**",
        f"Références images de la base experte résolues : **{image_links['verified']}/{image_links['total']}**",
        "",
        "## Bases de données SQLite",
        "",
    ]
    for name, data in report["databases"].items():
        lines.extend([
            f"### {name} — {data['status']}", "",
            f"Chemin : {data['path']}", "",
            "Tables : " + (
                ", ".join(f"{key}={count}" for key, count in data["tables"].items()) or
                "aucune table attendue détectée"
            ), "",
        ])
    lines.extend(["## Écarts bloquants", ""])
    lines.extend(f"- {s}" for s in gate["blockers"]) if gate["blockers"] else lines.append("- Aucun écart technique bloquant détecté")
    lines.extend(["", "## Contrôles restant à réaliser", ""])
    lines.extend(f"- {s}" for s in gate["not_yet_proven"])
    lines.extend(["", "## Documents source et correspondance avec Pack001", ""])
    matched = {row["pdf"]: row for row in mapping["matched"]}
    for d in o["records"]:
        link = matched.get(d["path"])
        state = (f"pack: {link['document_key']} ({link['proof']})"
                 if link else "PAS DE CORRESPONDANCE PROUVÉE")
        lines.append(
            f"- {d['path']} — {d['status']}, {d['pages']} pages, {state}"
        )
    lines.extend(["", "## Images absentes ou altérées", ""])
    errors = img["missing"] + img["hash_mismatch"] + img["unsafe_paths"]
    lines.extend(f"- {s}" for s in errors) if errors else lines.append("- Aucune parmi les références contrôlées")
    lines.extend(["", "Ce rapport mesure les fichiers et les index réellement observés, "
                  "pas la qualité sémantique de l'intégralité des PDF.", ""])
    return "\n".join(lines)


def write_report(report: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "audit_integralite.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "audit_integralite.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
