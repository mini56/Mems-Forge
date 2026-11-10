"""Source correction lots must be decoded faithfully, without rewriting them."""
from __future__ import annotations
import base64
from pathlib import Path
import zlib
from mems_forge.source_corrections import read_qz64_sql, inspect_1750


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
    try:
        read_qz64_sql(file)
    except (ValueError, zlib.error):
        pass
    else:
        raise AssertionError("Corrupt source lot must not be accepted")
