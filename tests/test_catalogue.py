"""Tests for an offline catalogue that cannot silently approve raw PDF data."""
from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pymupdf
import pytest

from mems_forge.catalogue import (
    inspect_candidate_catalogue, inventory_candidates, write_candidate_catalogue,
)
from mems_forge.document_pack import build_document_pack


@pytest.fixture
def candidate(tmp_path: Path) -> tuple[Path, Path]:
    directory = tmp_path / "packs"
    directory.mkdir()
    pdf = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 80), "MEMS lambda technical source")
        doc.save(pdf)
    pack = directory / "source.zip"
    build_document_pack(pdf, pack)
    return directory, pack


def test_verified_draft_has_hash_size_identity_and_no_download(candidate, tmp_path):
    directory, pack = candidate
    destination = tmp_path / "catalogue.json"
    result = write_candidate_catalogue(directory, destination)
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert result["pack_count"] == 1
    assert data["download_enabled"] is False
    assert data["publication_state"] == "DRAFT_NOT_PUBLISHED"
    entry = data["packs"][0]
    assert entry["pack_size_bytes"] == pack.stat().st_size
    assert len(entry["pack_sha256"]) == 64
    assert len(entry["source_sha256"]) == 64
    assert entry["source_pages"] == 1
    assert entry["approved_for_ai"] is False
    assert entry["redistribution_approved"] is False
    assert "download_url" not in entry
    assert inspect_candidate_catalogue(destination, directory)["valid"]
    assert write_candidate_catalogue(directory, destination)["sha256"] == result["sha256"]


def test_catalogue_fails_closed_after_pack_tampering(candidate, tmp_path):
    directory, pack = candidate
    destination = tmp_path / "catalogue.json"
    write_candidate_catalogue(directory, destination)
    with zipfile.ZipFile(pack, "a") as archive:
        archive.writestr("source/document.pdf", b"tampered")
    assert not inspect_candidate_catalogue(destination, directory)["valid"]


def test_catalogue_fails_closed_when_catalogue_edited(candidate, tmp_path):
    directory, _ = candidate
    destination = tmp_path / "catalogue.json"
    write_candidate_catalogue(directory, destination)
    data = json.loads(destination.read_text(encoding="utf-8"))
    data["download_enabled"] = True
    destination.write_text(json.dumps(data), encoding="utf-8")
    assert inspect_candidate_catalogue(destination, directory)["error"] == (
        "CATALOGUE_PACK_MISMATCH"
    )


def test_duplicate_source_is_rejected(candidate, tmp_path):
    directory, pack = candidate
    (directory / "second.zip").write_bytes(pack.read_bytes())
    with pytest.raises(ValueError, match="Duplicate source"):
        inventory_candidates(directory)
    assert not (tmp_path / "catalogue.json").exists()


def test_invalid_archive_never_creates_catalogue(tmp_path):
    directory = tmp_path / "packs"
    directory.mkdir()
    (directory / "broken.zip").write_bytes(b"not a ZIP")
    with pytest.raises(ValueError):
        write_candidate_catalogue(directory, tmp_path / "catalogue.json")
    assert not (tmp_path / "catalogue.json").exists()
