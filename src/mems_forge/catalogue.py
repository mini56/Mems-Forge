"""Immutable, offline-only inventory of verified MEMS Forge candidate ZIPs.

This is not a publication channel. It intentionally has no download URL or
"approved" state; humans must complete semantic and redistribution reviews
before a separate publication process can expose a pack to MEMS Manager.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

from .document_pack import inspect_document_pack


def sha256_file(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _pack_entry(pack: Path) -> dict:
    if not pack.is_file() or pack.is_symlink() or pack.suffix.lower() != ".zip":
        raise ValueError(f"Invalid candidate file: {pack}")
    verdict = inspect_document_pack(pack)
    if not verdict.get("valid"):
        raise ValueError(f"Invalid candidate pack {pack.name}: {verdict}")
    with zipfile.ZipFile(pack, "r") as archive:
        manifest = json.loads(archive.read("manifest.json"))
    if manifest.get("publication_state") != "CANDIDATE_NOT_VALIDATED":
        raise ValueError(f"Non-candidate publication state: {pack.name}")
    source = manifest["source"]
    return {
        "pack_id": manifest["pack_id"],
        "pack_file": pack.name,
        "pack_sha256": sha256_file(pack),
        "pack_size_bytes": pack.stat().st_size,
        "pack_format": manifest["format"],
        "pack_format_version": manifest["format_version"],
        "source_filename": source["filename"],
        "source_sha256": source["sha256"],
        "source_pages": source["pages"],
        "ocr_pages": manifest["counts"]["ocr_pages"],
        "dependencies": [],
        "validation_state": "CANDIDATE_NOT_VALIDATED",
        "approved_for_ai": False,
        "redistribution_approved": False,
    }


def inventory_candidates(pack_dir: Path) -> dict:
    """Inspect actual ZIP bytes; never trust filenames as identities."""
    pack_dir = Path(pack_dir)
    if not pack_dir.is_dir():
        raise ValueError(f"Candidate directory does not exist: {pack_dir}")
    candidates = sorted(pack_dir.glob("*.zip"), key=lambda p: p.name)
    if not candidates:
        raise ValueError("No candidate ZIP files found")
    entries = []
    by_source = {}
    by_pack = set()
    for pack in candidates:
        entry = _pack_entry(pack)
        source_sha = entry["source_sha256"]
        if source_sha in by_source:
            raise ValueError(
                f"Duplicate source SHA-256: {by_source[source_sha]} / {pack.name}"
            )
        if entry["pack_id"] in by_pack:
            raise ValueError(f"Duplicate pack ID: {entry['pack_id']}")
        by_source[source_sha] = pack.name
        by_pack.add(entry["pack_id"])
        entries.append(entry)
    return {
        "format": "mems-forge-candidate-catalogue",
        "format_version": 1,
        "publication_state": "DRAFT_NOT_PUBLISHED",
        "download_enabled": False,
        "requires": [
            "semantic_and_applicability_review",
            "page_and_visual_review",
            "redistribution_rights_review",
            "explicit_publication_approval",
        ],
        "packs": entries,
    }


def write_candidate_catalogue(pack_dir: Path, destination: Path) -> dict:
    """Publish a local *draft* atomically, never to a remote Release."""
    destination = Path(destination)
    catalogue = inventory_candidates(pack_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".candidate-catalogue-",
            suffix=".tmp", dir=destination.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(catalogue, stream, sort_keys=True, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {
        "path": str(destination),
        "sha256": sha256_file(destination),
        "pack_count": len(catalogue["packs"]),
        "publication_state": "DRAFT_NOT_PUBLISHED",
    }


def inspect_candidate_catalogue(catalogue_path: Path, pack_dir: Path) -> dict:
    """Reject a stale, edited, or substituted candidate catalogue."""
    try:
        stored = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
        actual = inventory_candidates(pack_dir)
        if stored != actual:
            return {"valid": False, "error": "CATALOGUE_PACK_MISMATCH"}
        return {
            "valid": True, "pack_count": len(actual["packs"]),
            "state": "DRAFT_NOT_PUBLISHED",
        }
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        return {"valid": False, "error": type(exc).__name__ + ": " + str(exc)}
