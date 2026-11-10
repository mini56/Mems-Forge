"""Source correction lots must be decoded faithfully, without rewriting them."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

import pytest

from mems_forge.source_corrections import (
    read_qz64_sql,
    inspect_1750,
    build_corrected_expert_database,
)


def test_qcompressed_sql_round_trip(tmp_path: Path) -> None:
    sql = "INSERT INTO proof VALUES ('RCL0194ENG:39.2');\n"
    binary = len(sql.encode()).to_bytes(4, "big") + zlib.compress(sql.encode())
    package = tmp_path / "database" / "reference"
    package.mkdir(parents=True)
    (package / "research_enrichment_1750.qz64").write_bytes(
        base64.b64encode(binary))
    report = inspect_1750(tmp_path)
    assert report["status"] == "OK"
    assert report["has_correct_page"] is True
    assert read_qz64_sql(package / "research_enrichment_1750.qz64") == sql


def test_truncated_qcompressed_sql_rejected(tmp_path: Path) -> None:
    file = tmp_path / "corrupt.qz64"
    file.write_bytes(b"AAAABw==")
    with pytest.raises((ValueError, zlib.error)):
        read_qz64_sql(file)


@pytest.fixture
def correction_package(tmp_path: Path):
    root = tmp_path / "setup"
    reference = root / "database" / "reference"
    image_dir = reference / "images" / "rave"
    image_dir.mkdir(parents=True)
    visual = {}
    for page in ("20.1", "20.2", "39.2"):
        image = image_dir / ("RCL0194ENG_" + page.replace(".", "_") + ".png")
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + page.encode())
        visual["rave:RCL0194ENG:" + page] = {
            "path": "images/rave/" + image.name,
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        }
    (reference / "manifest.json").write_text(
        json.dumps({"visual_assets": visual}), encoding="utf-8",
    )
    originals = [
        ("006", "RCL0194ENG:20.1,39.1", "20.1"),
        ("008", "RCL0194ENG:20.2,39.1", "20.2"),
        ("012", "RCL0194ENG:20.2,39.1", "20.2"),
    ]
    statements = []
    for suffix, old, first in originals:
        fact = "RAVE-WIR-MPI-" + suffix
        statements.append(
            "INSERT OR REPLACE INTO "
            "mems_rave_source_reference_correction("
            "correction_key,fact_key,original_image_ref,effective_image_ref,"
            "reason,verification_level,notes) VALUES("
            f"'RCL0194ENG:39.1->39.2:{fact}','{fact}','{old}',"
            "'RCL0194ENG:39.2','Factory source: page 39.1 is HEATER BLOWER; "
            "39.2 is COOLING FAN','verifie_constructeur','Historical evidence kept');"
        )
        for page, role in ((first, "source"), ("39.2", "effective_source")):
            statements.append(
                "INSERT OR IGNORE INTO mems_rave_illustration_link("
                "fact_key,illustration_key,link_role,notes) VALUES("
                f"'{fact}','RAVE:RCL0194ENG:{page}','{role}','Source evidence');"
            )
    sql = "\n".join(statements) + "\n"
    payload = sql.encode("utf-8")
    compressed = len(payload).to_bytes(4, "big") + zlib.compress(payload)
    lot = reference / "research_enrichment_1750.qz64"
    lot.write_bytes(base64.b64encode(compressed))
    verified_sha = hashlib.sha256(lot.read_bytes()).hexdigest()

    dbpath = root / "database" / "expert" / "ia_mems_reference_r20.sqlite"
    dbpath.parent.mkdir(parents=True)
    with sqlite3.connect(dbpath) as connection:
        connection.execute(
            "CREATE TABLE mems_knowledge_item("
            "knowledge_key TEXT PRIMARY KEY,image_ref TEXT,source_section TEXT)"
        )
        for suffix, old, first in originals:
            connection.execute(
                "INSERT INTO mems_knowledge_item VALUES(?,?,?)",
                ("KNOW-RAVE-WIR-MPI-" + suffix, old,
                 "MPi source " + first + "; cooling fan source 39.1"),
            )
    return root, verified_sha, dbpath


def test_build_source_corrected_sqlite_without_mutating_original(
    correction_package, tmp_path: Path,
) -> None:
    source_root, checksum, original = correction_package
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    target = tmp_path / "out" / "expert.corrected.sqlite"
    result = build_corrected_expert_database(
        source_root, target,
        expected_lot_sha256=checksum,
        evidence_report=tmp_path / "correction_report.json",
    )
    assert len(result["corrected_records"]) == 3
    assert result["state"] == "CORRECTED_CANDIDATE_NOT_YET_PUBLISHED"
    assert target.exists()
    assert (tmp_path / "correction_report.json").exists()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before

    with sqlite3.connect(target) as conn:
        rows = conn.execute(
            "SELECT knowledge_key,image_ref,source_section "
            "FROM mems_knowledge_item ORDER BY knowledge_key"
        ).fetchall()
        assert rows == [
            ("KNOW-RAVE-WIR-MPI-006", "RCL0194ENG:20.1,39.2",
             "MPi source 20.1; cooling fan source 39.2"),
            ("KNOW-RAVE-WIR-MPI-008", "RCL0194ENG:20.2,39.2",
             "MPi source 20.2; cooling fan source 39.2"),
            ("KNOW-RAVE-WIR-MPI-012", "RCL0194ENG:20.2,39.2",
             "MPi source 20.2; cooling fan source 39.2"),
        ]
        old_refs = conn.execute(
            "SELECT legacy_image_ref FROM mems_source_correction_history "
            "ORDER BY knowledge_key"
        ).fetchall()
        assert old_refs == [
            ("RCL0194ENG:20.1,39.1",),
            ("RCL0194ENG:20.2,39.1",),
            ("RCL0194ENG:20.2,39.1",),
        ]
    with sqlite3.connect(original) as conn:
        refs = conn.execute("SELECT image_ref FROM mems_knowledge_item").fetchall()
        assert all("39.1" in row[0] for row in refs)


def test_unexpected_lot_hash_aborts_atomically(
    correction_package, tmp_path: Path,
) -> None:
    source_root, checksum, original = correction_package
    target = tmp_path / "output.sqlite"
    with pytest.raises(ValueError, match="source correction lot invalid"):
        build_corrected_expert_database(
            source_root, target, expected_lot_sha256="0" * 64,
        )
    assert not target.exists()


def test_missing_effective_image_blocks_candidate(
    correction_package, tmp_path: Path,
) -> None:
    source_root, checksum, original = correction_package
    (source_root / "database" / "reference" / "images" /
     "rave" / "RCL0194ENG_39_2.png").unlink()
    target = tmp_path / "candidate.sqlite"
    with pytest.raises(ValueError, match="Corrected image absent"):
        build_corrected_expert_database(
            source_root, target, expected_lot_sha256=checksum,
        )
    assert not target.exists()


def test_unexpected_legacy_value_aborts_without_partial_output(
    correction_package, tmp_path: Path,
) -> None:
    source_root, checksum, original = correction_package
    with sqlite3.connect(original) as conn:
        conn.execute(
            "UPDATE mems_knowledge_item SET image_ref=? WHERE knowledge_key=?",
            ("RCL0194ENG:20.1", "KNOW-RAVE-WIR-MPI-006"),
        )
    target = tmp_path / "candidate.sqlite"
    with pytest.raises(ValueError, match="Unexpected old image ref"):
        build_corrected_expert_database(
            source_root, target, expected_lot_sha256=checksum,
        )
    assert not target.exists()
