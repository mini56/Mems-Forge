"""Direct read-only SQLite Database Tester, independent of MEMS Manager AI."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from .package_audit import _readonly_sqlite, resolve_image_references

# These are existing, independently populated database schemas. A query cannot
# choose arbitrary SQL identifiers or modify an installed database.
SEARCH_CONFIG: dict[str, tuple[str, ...]] = {
    "memslibrary_search": ("title", "body", "document_key", "entity_key"),
    "search_documents": ("title", "content", "source_key"),
    "mems_knowledge_item": ("knowledge_key", "topic", "source_text", "image_ref", "document"),
    "documents": ("document_id", "title", "manufacturer"),
    "structured_entities": ("entity_id", "entity_type", "payload_json"),
}
DISPLAY_FIELDS: dict[str, tuple[str, ...]] = {
    "memslibrary_search": (
        "document_key", "revision_key", "page_number", "source_language",
        "entity_kind", "entity_key", "title", "body",
    ),
    "search_documents": (
        "source_table", "source_key", "category", "generation", "title", "content",
    ),
    "mems_knowledge_item": (
        "knowledge_key", "document", "source_section", "source_text", "image_ref",
        "verification_level", "topic",
    ),
    "documents": ("document_id", "title", "manufacturer"),
    "structured_entities": ("entity_id", "entity_type", "payload_json", "validation_state"),
}


def search_database(
    path: Path, term: str, *, limit: int = 20, installed_root: Path | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "database": str(path), "query": term, "results": [], "searched_tables": [],
        "errors": [],
    }
    if not path.is_file():
        result["errors"].append("BASE_ABSENTE")
        return result
    if not term.strip():
        result["errors"].append("REQUETE_VIDE")
        return result

    remaining = max(1, min(50, limit))
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = "%" + escaped + "%"
    try:
        with _readonly_sqlite(path) as db:
            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table, candidate_columns in SEARCH_CONFIG.items():
                if table not in tables or remaining < 1:
                    continue
                cols = {
                    row[1] for row in db.execute(f'PRAGMA table_info("{table}")')
                }
                match_columns = [name for name in candidate_columns if name in cols]
                if not match_columns:
                    continue
                fields = [name for name in DISPLAY_FIELDS[table] if name in cols]
                if not fields:
                    continue
                # Identifiers are from constant trusted allowlists.
                selects = ", ".join(f'"{name}"' for name in fields)
                conditions = " OR ".join(
                    f'CAST("{name}" AS TEXT) LIKE ? ESCAPE \'\\\'' for name in match_columns
                )
                args: list[Any] = [pattern] * len(match_columns)
                args.append(remaining)
                query = f'SELECT {selects} FROM "{table}" WHERE ({conditions}) LIMIT ?'
                result["searched_tables"].append(table)
                for row in db.execute(query, args):
                    entry = {
                        "source_table": table, "fields": dict(row),
                        "source_file_found": None,
                    }
                    if installed_root is not None:
                        value = entry["fields"].get("image_ref")
                        if value:
                            parts = resolve_image_references(installed_root, value)
                            available = [p for p in parts if p["status"] == "OK"]
                            missing = [p for p in parts if p["status"] != "OK"]
                            entry["source_file_found"] = bool(parts) and not missing
                            entry["image_reference_status"] = (
                                "OK" if not missing else
                                "PARTIAL" if available else "NOT_RESOLVED"
                            )
                            entry["source_files"] = [p["path"] for p in available]
                            entry["missing_image_parts"] = [
                                p["reference"] for p in missing
                            ]
                            if available:
                                entry["source_file"] = available[0]["path"]
                    result["results"].append(entry)
                    remaining -= 1
                    if remaining == 0:
                        break
    except (OSError, sqlite3.Error) as exc:
        result["errors"].append(str(exc))
    result["count"] = len(result["results"])
    return result


def search_installed_databases(
    install_root: Path, term: str, *, limit: int = 20
) -> dict[str, Any]:
    """Search Pack001 and the expert database in one request, read-only.

    These are separate existing databases with distinct schemas. A query that
    misses Pack001 must still reach the expert SQLite, not be declared empty.
    """
    paths = [
        ("memslibrary_pack", install_root / "MEMSLibrary_Pack_001" / "knowledge.sqlite"),
        ("expert_database", install_root / "database" / "expert" / "ia_mems_reference_r20.sqlite"),
    ]
    found: dict[str, Any] = {
        "query": term, "results": [], "bases": [], "errors": [],
    }
    max_results = max(1, min(50, limit))
    for name, path in paths:
        partial = search_database(
            path, term, limit=max_results, installed_root=install_root,
        )
        found["bases"].append({
            "name": name, "path": str(path), "hits": partial.get("count", 0),
            "errors": partial["errors"],
        })
        if partial["errors"]:
            found["errors"].append({name: partial["errors"]})
        for row in partial["results"]:
            if len(found["results"]) < max_results:
                found["results"].append({"source_database": name, **row})
    found["count"] = len(found["results"])
    return found
