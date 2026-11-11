"""Multi-page repair continuity is source-traced, never an automatic merge."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pymupdf
import pytest

from mems_forge.document_pack import build_document_pack, inspect_document_pack
from mems_forge.procedure_fragments import _adjacency_candidates


def _fragment(page: int, first: int, last: int,
              *, phase: str | None = None) -> dict:
    entries = []
    for number in range(first, last + 1):
        source_text = f"{number}. Verified transcription candidate {number}"
        entries.append({
            "number": number,
            "text": source_text,
            "source": {
                "region": "left",
                "source_line_key": [page, 1, number],
                "bbox": [20, 25 + number * 20, 240, 38 + number * 20],
                "mean_confidence": 92.0,
                "text": source_text,
            },
        })
    return {
        "kind": "continuation_fragment_candidate",
        "status": "REVIEW_REQUIRED",
        "region": "left",
        "page_number": page,
        "first_step_number": first,
        "last_step_number": last,
        "step_numbers": list(range(first, last + 1)),
        "phase_markers": [{"label": phase}] if phase else [],
        "numbered_lines": entries,
    }


def _procedure(number: str = "70.35.10") -> dict:
    return {
        "repair_number": number,
        "region": "left",
        "steps": [
            {"number": 1, "phase": "remove"},
            {"number": 2, "phase": "remove"},
            {"number": 3, "phase": "remove"},
        ],
    }


def _links(
    *, physical: dict[int, int] | None = None,
    chapters: dict[int, dict] | None = None,
    structures: dict[int, dict] | None = None,
    page2: dict | None = None,
    include_page3: bool = True,
):
    details = [
        {"page_number": 1, "fragments": []},
        {"page_number": 2, "fragments": [
            page2 if page2 is not None else _fragment(2, 4, 5),
        ]},
    ]
    if include_page3:
        details.append({"page_number": 3, "fragments": [_fragment(3, 6, 7)]})
    contexts = chapters if chapters is not None else {
        n: {"chapter_id": "chapter-manufacturer-brakes"} for n in
        range(1, len(details) + 1)
    }
    numbers = physical if physical is not None else {
        n: 120 + n - 1 for n in range(1, len(details) + 1)
    }
    structure = structures if structures is not None else {
        1: {"procedures": [_procedure()]},
    }
    return _adjacency_candidates(
        details, contexts, structure, numbers, "a" * 64
    )


def test_3_consecutive_original_pages_propose_one_repair_not_three():
    links = _links()
    assert [(x["from_original_physical_page"],
             x["to_original_physical_page"]) for x in links] == [
        (120, 121), (121, 122),
    ]
    assert [x["fragment_step_numbers"] for x in links] == [
        [4, 5], [6, 7],
    ]
    assert {x["candidate_repair_number"] for x in links} == {"70.35.10"}
    assert all(x["approved_for_ai"] is False for x in links)
    assert all(x["status"] == "REVIEW_REQUIRED" for x in links)
    assert links[1]["origin_procedure_page"] == 1


def test_nonadjacent_selected_pages_can_never_be_linked():
    assert _links(physical={1: 116, 2: 129, 3: 304}) == []


def test_chapter_break_blocks_procedure_crossing():
    chapters = {
        1: {"chapter_id": "engine"},
        2: {"chapter_id": "suspension"},
        3: {"chapter_id": "suspension"},
    }
    assert _links(chapters=chapters) == []


def test_ambiguous_two_preceding_repairs_never_get_assigned():
    assert _links(
        structures={1: {"procedures": [_procedure(), _procedure("70.35.11")]}},
    ) == []


def test_refit_heading_can_restart_1_as_review_only():
    links = _links(
        page2=_fragment(2, 1, 3, phase="refit"),
        include_page3=False,
    )
    assert len(links) == 1
    assert "explicit_refit_phase_restart" in links[0]["evidence"]
    assert links[0]["approved_for_ai"] is False


def test_reset_without_manufacturer_phase_heading_does_not_link():
    assert _links(
        page2=_fragment(2, 1, 3),
        include_page3=False,
    ) == []


def test_missing_source_chapter_does_not_create_invented_links():
    assert _links(chapters={}, include_page3=False) == []


def test_duplicate_original_physical_page_is_hard_error():
    with pytest.raises(ValueError, match="PHYSICAL_PAGE_MAPPING_NOT_STRICTLY_INCREASING"):
        _links(physical={1: 120, 2: 120, 3: 122})


def _write_candidate_procedure_report(
    tmp_path: Path, sha: str, *,
    mismatched_page_map: bool = False,
) -> Path:
    details = [
        {"page_number": 1, "fragments": []},
        {"page_number": 2, "fragments": [_fragment(2, 4, 5)]},
        {"page_number": 3, "fragments": [_fragment(3, 6, 7)]},
    ]
    chapter = {n: {"chapter_id": "chapter-manufacturer-brakes"}
               for n in range(1, 4)}
    original_map = {1: 120, 2: 121, 3: 122}
    links = _adjacency_candidates(
        details, chapter, {1: {"procedures": [_procedure()]}},
        original_map, sha,
    )
    root = tmp_path / "fragments"
    pages = root / sha / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    for page in details:
        (pages / f"page-{page['page_number']:04d}.json").write_text(
            json.dumps(page), encoding="utf-8",
        )
    if mismatched_page_map:
        original_map[2] = 129
    summary = {
        "source_pdf_sha256": sha,
        "automatic_procedure_merges": 0,
        "publication_gate": "BLOCKED_REVIEW",
        "original_physical_page_map": {
            str(key): value for key, value in original_map.items()
        },
        "cross_page_link_candidate_count": len(links),
        "cross_page_link_candidates": links,
    }
    (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return root


def _pdf3(tmp_path: Path) -> tuple[Path, str]:
    pdf_path = tmp_path / "workshop.pdf"
    with pymupdf.open() as pdf:
        for number in range(1, 4):
            pdf.new_page().insert_text(
                (50, 70), f"Workshop source page {number}"
            )
        pdf.save(pdf_path)
    return pdf_path, hashlib.sha256(pdf_path.read_bytes()).hexdigest()


def test_3_page_continuations_saved_in_candidate_sqlite(tmp_path: Path):
    source, sha = _pdf3(tmp_path)
    fragments = _write_candidate_procedure_report(tmp_path, sha)
    pack = tmp_path / "unapproved-candidate.zip"
    result = build_document_pack(source, pack, fragment_root=fragments)
    assert result["manifest"]["counts"]["procedure_continuation_candidates"] == 2
    assert inspect_document_pack(pack)["valid"]
    with zipfile.ZipFile(pack) as zipfile_source:
        dbfile = tmp_path / "test.sqlite"
        dbfile.write_bytes(zipfile_source.read("data/knowledge.sqlite"))
    with sqlite3.connect(dbfile) as db:
        rows = db.execute(
            "SELECT from_original_page,to_original_page,candidate_repair_number,"
            "validation_state,approved_for_ai,step_numbers_json "
            "FROM pack_procedure_continuation_candidates "
            "ORDER BY from_original_page"
        ).fetchall()
        assert rows == [
            (120, 121, "70.35.10", "unresolved", 0, "[4, 5]"),
            (121, 122, "70.35.10", "unresolved", 0, "[6, 7]"),
        ]
        assert db.execute(
            "SELECT COUNT(*) FROM structured_entities"
        ).fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    from mems_forge.db_tester import search_database
    found = search_database(dbfile, "70.35.10")
    assert any(
        x["source_table"] == "pack_procedure_continuation_candidates"
        for x in found["results"]
    )


def test_forged_original_page_map_is_rejected_by_pack_builder(tmp_path: Path):
    source, sha = _pdf3(tmp_path)
    fragments = _write_candidate_procedure_report(
        tmp_path, sha, mismatched_page_map=True,
    )
    destination = tmp_path / "blocked.zip"
    with pytest.raises(ValueError, match="CONTINUATION_"):
        build_document_pack(source, destination, fragment_root=fragments)
    assert not destination.exists()
