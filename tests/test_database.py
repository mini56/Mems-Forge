from pathlib import Path

from mems_forge import __version__
from mems_forge.database import initialize_database, validate_database


def test_initialize_and_validate_database(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "mems_forge.db", __version__)
    result = validate_database(db_path)

    assert result["integrity_ok"] is True
    assert result["foreign_key_errors"] == 0
    assert result["pending_reviews"] == 0
    assert result["publication_blocked"] is False
    assert result["metadata"]["schema_version"] == "1"


def test_pending_review_blocks_publication(tmp_path: Path) -> None:
    db_path = initialize_database(tmp_path / "mems_forge.db", __version__)

    import sqlite3

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO review_items(review_id, severity, reason_code, reason_text) "
            "VALUES('review-1', 'high', 'TEST', 'Donnée à valider')"
        )
        connection.commit()

    result = validate_database(db_path)
    assert result["pending_reviews"] == 1
    assert result["publication_blocked"] is True
