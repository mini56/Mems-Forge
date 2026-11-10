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


# A verified installed source lot, not a guessed filename conversion.
APPROVED_1750_SHA256 = "cd45d2172d6c65422baee2f379c45d0956f08f5623076b86ea4f434c834ca999"
FACTORY_RCL0194ENG_SHA256 = (
    "64e64f8a7c24f362913e2661403bc474e4e7ef07f96db618ef661645e0d0f051"
)


def effective_source_references(
    install_root: Path, *, expected_lot_sha256: str = APPROVED_1750_SHA256,
) -> dict[str, object]:
    """Read verified effective diagram links from historical lot 1750.

    Only source correction SQL and link declarations are parsed, never
    executed. The legacy source stays intact and is explicitly reported.
    A changed/invalid correction lot is rejected, not silently trusted.
    """
    path = install_root / "database" / "reference" / "research_enrichment_1750.qz64"
    result: dict[str, object] = {
        "status": "MISSING", "source": str(path), "entries": {},
    }
    if not path.is_file():
        return result
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    result["sha256"] = actual_sha
    result["factory_document_sha256"] = FACTORY_RCL0194ENG_SHA256
    if actual_sha != expected_lot_sha256:
        result["status"] = "UNEXPECTED_SOURCE_REVISION"
        return result
    try:
        sql = read_qz64_sql(path)
    except (ValueError, OSError, UnicodeError, zlib.error) as exc:
        result["status"] = "INVALID"
        result["error"] = str(exc)
        return result

    corrections: dict[str, dict[str, object]] = {}
    links: dict[str, list[tuple[str, str]]] = {}
    for line in sql.splitlines():
        trimmed = line.strip()
        if trimmed.startswith(
            "INSERT OR REPLACE INTO mems_rave_source_reference_correction("
        ):
            values = [
                match.replace("''", "'") for match in
                re.findall(r"'((?:[^']|'')*)'", trimmed)
            ]
            if len(values) != 7:
                result["status"] = "INVALID_CORRECTION_SQL"
                return result
            corr_key, fact, original, effective, reason, verification, notes = values
            corrections[fact] = {
                "source_correction_id": corr_key,
                "fact_key": fact,
                "legacy_image_ref": original,
                "effective_source_ref": effective,
                "reason": reason,
                "verification_level": verification,
                "notes": notes,
            }
        elif trimmed.startswith(
            "INSERT OR IGNORE INTO mems_rave_illustration_link("
        ):
            values = [
                match.replace("''", "'") for match in
                re.findall(r"'((?:[^']|'')*)'", trimmed)
            ]
            if len(values) == 4:
                fact, illustration_key, role, _notes = values
                if role in {"source", "effective_source"} and illustration_key.startswith("RAVE:"):
                    links.setdefault(fact, []).append(
                        (illustration_key[len("RAVE:"):], role)
                    )

    for fact, correction in corrections.items():
        present = links.get(fact, [])
        # Do not retain legacy 39.1 as a usable effective source.
        effective = [name for name, role in present
                     if role in {"source", "effective_source"}]
        if (correction["effective_source_ref"] not in effective or
                correction["verification_level"] != "verifie_constructeur"):
            result["status"] = "INVALID_CORRECTION_LINKS"
            return result
        correction["effective_image_refs"] = list(dict.fromkeys(effective))
        result["entries"]["KNOW-" + fact] = correction

    if len(result["entries"]) != 3:
        result["status"] = "CORRECTION_COUNT_MISMATCH"
        return result
    result["status"] = "OK"
    return result
