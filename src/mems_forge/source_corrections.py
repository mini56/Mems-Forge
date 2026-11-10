"""Inspect original, validated qCompress SQL correction lots read-only.

The source package's research_enrichment_1750.qz64 already records three
explicit historical RCL0194ENG page corrections. This inspector preserves
their exact SQL evidence, rather than silently replacing an old citation.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import sqlite3
import tempfile
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



def build_corrected_expert_database(
    install_root: Path, destination: Path, *,
    evidence_report: Path | None = None,
    expected_lot_sha256: str = APPROVED_1750_SHA256,
) -> dict[str, object]:
    """Build a corrected SQLite database from the *original* in one transaction.

    The source expert database is never modified. Its three legacy image_refs
    and source_section values are replaced by the already validated effective
    factory citations from lot 1750. A durable history table preserves every
    old value and the exact correction evidence. Unexpected data or missing
    images abort the entire candidate; nothing partial is published.
    """
    from .package_audit import resolve_image_reference

    source = (install_root / "database" / "expert" /
              "ia_mems_reference_r20.sqlite")
    destination = Path(destination)
    correction_lot = effective_source_references(
        install_root, expected_lot_sha256=expected_lot_sha256,
    )
    if correction_lot["status"] != "OK":
        raise ValueError(
            f"RCL0194 source correction lot invalid: {correction_lot['status']}"
        )
    if not source.is_file():
        raise FileNotFoundError(f"Original expert database absent: {source}")
    if source.resolve() == destination.resolve():
        raise ValueError("Refuse to overwrite the original expert database")
    if destination.exists():
        raise FileExistsError(f"Candidate output already exists: {destination}")

    verified_entries = []
    for key, record in sorted(correction_lot["entries"].items()):
        refs = list(record["effective_image_refs"])
        documents = [s.split(":", 1)[0] for s in refs]
        if not refs or len(set(documents)) != 1:
            raise ValueError(f"Multiple source documents for {key}")
        ref_parts = [s.split(":", 1)[1] for s in refs]
        effective_ref = documents[0] + ":" + ",".join(ref_parts)
        for ref in refs:
            actual = resolve_image_reference(install_root, ref)
            if actual["status"] != "OK":
                raise ValueError(
                    f"Corrected image absent or invalid: {ref}: {actual['status']}"
                )
        verified_entries.append((key, record, effective_ref))

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".mems-forge-", suffix=".sqlite",
        dir=destination.parent, delete=False
    ) as tmp:
        temp_file = Path(tmp.name)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        shutil.copy2(source, temp_file)
        updates = []
        with sqlite3.connect(temp_file) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS mems_source_correction_history(
                correction_id TEXT PRIMARY KEY,
                knowledge_key TEXT UNIQUE NOT NULL,
                legacy_image_ref TEXT NOT NULL,
                effective_image_ref TEXT NOT NULL,
                legacy_source_section TEXT,
                effective_source_section TEXT,
                verification_level TEXT NOT NULL,
                manufacturer_reason TEXT NOT NULL,
                correction_lot_sha256 TEXT NOT NULL,
                factory_pdf_sha256 TEXT NOT NULL
            )""")
            for key, record, new_image_ref in verified_entries:
                row = db.execute(
                    "SELECT image_ref,source_section FROM mems_knowledge_item "
                    "WHERE knowledge_key=?", (key,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"Expert knowledge record absent: {key}")
                old_image_ref, old_section = row
                if old_image_ref != record["legacy_image_ref"]:
                    raise ValueError(
                        f"Unexpected old image ref for {key}: {old_image_ref}"
                    )
                old_section = old_section or ""
                new_section = old_section.replace("39.1", "39.2")
                if old_section == new_section:
                    raise ValueError(
                        f"Historical 39.1 source section absent for {key}"
                    )
                result = db.execute(
                    "UPDATE mems_knowledge_item SET image_ref=?,source_section=? "
                    "WHERE knowledge_key=? AND image_ref=?",
                    (new_image_ref, new_section, key, old_image_ref),
                )
                if result.rowcount != 1:
                    raise ValueError(f"Correction update failed for {key}")
                db.execute(
                    "INSERT INTO mems_source_correction_history VALUES("
                    "?,?,?,?,?,?,?,?,?,?)",
                    (
                        record["source_correction_id"], key,
                        old_image_ref, new_image_ref,
                        old_section, new_section,
                        record["verification_level"], record["reason"],
                        correction_lot["sha256"],
                        correction_lot["factory_document_sha256"],
                    )
                )
                updates.append({
                    "knowledge_key": key,
                    "source_correction_id": record["source_correction_id"],
                    "previous_image_ref": old_image_ref,
                    "effective_image_ref": new_image_ref,
                    "previous_source_section": old_section,
                    "effective_source_section": new_section,
                })
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok" or foreign_keys:
                raise ValueError(
                    f"Updated SQLite integrity failure: {integrity}, "
                    f"foreign keys={len(foreign_keys)}"
                )
            db.commit()
        if len(updates) != 3:
            raise ValueError("Expected exactly 3 verified factory citations")
        os.replace(temp_file, destination)
    finally:
        temp_file.unlink(missing_ok=True)

    report: dict[str, object] = {
        "state": "CORRECTED_CANDIDATE_NOT_YET_PUBLISHED",
        "source_database": str(source),
        "candidate_database": str(destination),
        "source_sha256": original_hash,
        "candidate_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "correction_lot_sha256": correction_lot["sha256"],
        "factory_document_sha256": correction_lot["factory_document_sha256"],
        "corrected_records": updates,
        "historical_citations_preserved": True,
        "source_modified": False,
    }
    if evidence_report is not None:
        evidence_report.parent.mkdir(parents=True, exist_ok=True)
        evidence_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report
