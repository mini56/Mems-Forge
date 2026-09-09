from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import SCHEMA_VERSION, apply_schema

SUPPORTED_LANGUAGES = ("fr", "en", "it", "es", "de", "pt", "ja", "hi")


def connect(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path: str | Path, forge_version: str) -> Path:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with connect(db_path) as connection:
        apply_schema(connection)
        metadata = {
            "forge_version": forge_version,
            "schema_version": str(SCHEMA_VERSION),
            "publication_state": "development",
            "supported_languages": ",".join(SUPPORTED_LANGUAGES),
        }
        connection.executemany(
            "INSERT OR REPLACE INTO database_metadata(key, value) VALUES(?, ?)",
            metadata.items(),
        )
        connection.commit()

    return db_path


def validate_database(path: str | Path) -> dict[str, object]:
    with connect(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        metadata_rows = connection.execute(
            "SELECT key, value FROM database_metadata ORDER BY key"
        ).fetchall()
        pending_reviews = connection.execute(
            "SELECT COUNT(*) FROM review_items WHERE status = 'pending'"
        ).fetchone()[0]
        critical_pending_reviews = connection.execute(
            "SELECT COUNT(*) FROM review_items "
            "WHERE status = 'pending' AND severity = 'critical'"
        ).fetchone()[0]

    return {
        "integrity_ok": integrity == "ok",
        "integrity_result": integrity,
        "foreign_key_errors": len(foreign_key_errors),
        "pending_reviews": pending_reviews,
        "critical_pending_reviews": critical_pending_reviews,
        "publication_blocked": pending_reviews > 0,
        "metadata": {row["key"]: row["value"] for row in metadata_rows},
    }
