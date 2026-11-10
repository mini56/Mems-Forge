"""Inspect original, validated qCompress SQL correction lots read-only.

The source package's research_enrichment_1750.qz64 already records three
explicit historical RCL0194ENG page corrections. This inspector preserves
their exact SQL evidence, rather than silently replacing an old citation.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import zlib


RELEVANT_TERMS = ("39.1", "39.2", "RAVE-WIR-MPI-", "source_correction")


def read_qz64_sql(path: Path) -> str:
    encoded = path.read_bytes().strip()
    if re.fullmatch(rb"[A-Za-z0-9+/\s=]+", encoded):
        compressed = base64.b64decode(encoded, validate=False)
    else:
        compressed = encoded
    if len(compressed) < 5:
        raise ValueError("Qt compressed payload too short")
    expected = int.from_bytes(compressed[:4], "big")
    sql = zlib.decompress(compressed[4:])
    if not sql or len(sql) != expected:
        raise ValueError("Qt compressed SQL length mismatch")
    return sql.decode("utf-8")


def inspect_1750(package_root: Path) -> dict[str, object]:
    path = package_root / "database" / "reference" / "research_enrichment_1750.qz64"
    report: dict[str, object] = {"source": str(path), "status": "MISSING", "statements": []}
    if not path.is_file():
        return report
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    report["sha256"] = digest
    try:
        script = read_qz64_sql(path)
    except (OSError, ValueError, zlib.error, UnicodeError) as exc:
        report["status"] = "INVALID"
        report["error"] = str(exc)
        return report
    lines = [
        x.strip() for x in script.splitlines()
        if any(term.lower() in x.lower() for term in RELEVANT_TERMS)
    ]
    report.update({
        "status": "OK",
        "sql_length": len(script),
        "relevant_line_count": len(lines),
        "statements": [x[:750] for x in lines[:60]],
        "has_correct_page": "39.2" in script,
        "has_legacy_page": "39.1" in script,
    })
    return report


def write_inspection(package_root: Path, output: Path) -> dict[str, object]:
    result = inspect_1750(package_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n",
                      encoding="utf-8")
    return result
