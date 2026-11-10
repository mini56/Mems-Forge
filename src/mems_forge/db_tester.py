"""Direct read-only SQLite Database Tester, independent of MEMS Manager AI."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from .package_audit import _readonly_sqlite, _safe_file

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
                            image = _safe_file(installed_root / "database" / "reference", value)
                            entry["source_file_found"] = bool(image and image.is_file())
                            if image and image.is_file():
                                entry["source_file"] = str(image)
                    result["results"].append(entry)
                    remaining -= 1
                    if remaining == 0:
                        break
    except (OSError, sqlite3.Error) as exc:
        result["errors"].append(str(exc))
    result["count"] = len(result["results"])
    return result
