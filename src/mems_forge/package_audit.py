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


def _pack_comparison(originals: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    pack_names = pack.get("document_keys", [])
    normalized = {}
    for key in pack_names:
        normalized.setdefault(normalized_key(key), []).append(key)
    matched = []
    unmatched = []
    for row in originals["records"]:
        stem = normalized_key(Path(row["path"]).stem)
        matches = normalized.get(stem, [])
        if len(matches) == 1:
            key = matches[0]
            indexed_pages = pack.get("pages_by_document", {}).get(key)
            source_pages = pack.get("source_pages_by_document", {}).get(key)
            matched.append({
                "pdf": row["path"], "document_key": key,
                "original_pages": row["pages"],
                "pages_in_search_index": indexed_pages,
                "pages_in_source_table": source_pages,
                "source_page_coverage": "UNVERIFIED" if source_pages is None or row["pages"] is None
                    else "COMPLETE_COUNT" if source_pages == row["pages"] else "COUNT_MISMATCH",
            })
        else:
            unmatched.append({
                "pdf": row["path"], "candidate_keys": matches,
                "reason": "missing_in_pack" if not matches else "ambiguous_in_pack",
            })
    return {
        "pack_document_keys": pack_names, "count_in_pack": len(pack_names),
        "matched": matched, "unmatched": unmatched,
        "unmatched_count": len(unmatched),
        "notes": "Identite stricte par nom de PDF / cle documentaire; "
                 "une egalite de comptes ne prouve pas la couverture.",
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
    if any(item.get("error") for item in extra_pdfs):
        blockers.append("ADDITIONAL_PDF_INPUT_UNREADABLE")

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
    gate = report["publication_gate"]
    lines = [
        "# MEMS Forge — Audit de la documentation réellement installée",
        "",
        f"État : **{gate['state']}**",
        f"PDF originaux vérifiés : **{o['ok']}/{o['expected']}**",
        f"Pages des PDF vérifiés : **{o['total_pages']}**",
        f"Documents reliés au Pack001 : **{len(mapping['matched'])}/{o['expected']}**",
        f"Documents non reliés : **{mapping['unmatched_count']}**",
        f"Images installées : **{img['available_images']}/{img['expected_images']}**",
        f"Images contrôlées par SHA-256 : **{img['sha256_verified']}**",
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
        state = f"pack: {link['document_key']}" if link else "PAS DE CORRESPONDANCE PROUVÉE"
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
