"""OCR accuracy must be evaluated against real image layout, not its confidence alone."""
from __future__ import annotations

from mems_forge.ocr_strategy_audit import _numbers


def test_numbering_duplicates_and_holes_never_pass_undetected():
    stats = _numbers("1. sensor\n2. valve\n3. coil\n3. injector\n5. ECU")
    assert stats["numbers"] == [1, 2, 3, 3, 5]
    assert stats["repeats"] == [3]
    assert stats["gaps"] == [4]


def test_numbering_empty_is_not_claimed_valid():
    stats = _numbers("SOMNDAEWN=\nOxygen sensor\nInertia switch")
    assert stats["numbers"] == []
    assert stats["starts_at_one"] is False


def test_sequential_list_has_no_numbering_conflicts():
    stats = _numbers("1. Oxygen sensor\n2. Inertia switch\n3. Fuel pump")
    assert stats["numbers"] == [1, 2, 3]
    assert stats["repeats"] == []
    assert stats["gaps"] == []


def test_list_positions_come_from_original_pdf_tsv_not_ai():
    import pymupdf
    from mems_forge.ocr_strategy_audit import _numbered_lines_with_pdf_bboxes
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
    )
    lines = [
        "5\t1\t1\t1\t1\t1\t20\t30\t14\t12\t92\t1.",
        "5\t1\t1\t1\t1\t2\t35\t30\t100\t12\t95\tOxygen",
        "5\t1\t1\t1\t1\t3\t140\t30\t90\t12\t95\tsensor",
        "5\t1\t1\t1\t2\t1\t20\t60\t14\t12\t92\t2.",
        "5\t1\t1\t1\t2\t2\t35\t60\t100\t12\t95\tInjector",
    ]
    items, warnings = _numbered_lines_with_pdf_bboxes(
        header + "\n".join(lines) + "\n", 1,
        pymupdf.Rect(40, 50, 400, 500), 400,
    )
    assert not warnings
    assert [v["source_text"] for v in items] == [
        "1. Oxygen sensor", "2. Injector",
    ]
    assert items[0]["bbox_pdf"] == [
        40 + 20 * 72 / 400,
        50 + 30 * 72 / 400,
        40 + 230 * 72 / 400,
        50 + 42 * 72 / 400,
    ]
    assert all(item["approved_for_ai"] is False for item in items)


def test_remove_refit_numbering_restarthas_independent_phase_segments():
    from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps
    text = (
        "Remove\n"
        "1. Undo damper fixing\n"
        "2. Remove damper\n"
        "Refit\n"
        "1. Position damper\n"
        "2. Tighten mounting\n"
    )
    segments = _phase_bounded_numbered_steps(text)
    assert [(s["phase"], s["step_numbers"], s["sequence_status"])
            for s in segments] == [
        ("remove", [1, 2], "contiguous"),
        ("refit", [1, 2], "contiguous"),
    ]
    assert all(not s["approved_for_technical_database"] for s in segments)
    assert all(not step["approved_for_ai"]
               for seg in segments for step in seg["steps"])


def test_unassigned_numbers_never_receive_invented_phase():
    from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps
    segments = _phase_bounded_numbered_steps(
        "1. Left column diagram callout\n2. Other source text\nRefit\n"
        "1. Fit part\n"
    )
    assert [s["phase"] for s in segments] == ["unassigned", "refit"]
    assert not segments[0]["source_heading_explicit"]
    assert segments[0]["step_numbers"] == [1, 2]


def test_non_contiguous_steps_still_report_unresolved_error():
    from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps
    segments = _phase_bounded_numbered_steps("Remove\n1. Start\n3. End\n")
    assert segments[0]["sequence_status"] == "gaps_repeats_or_ocr_conflict"


def test_image_callout_numbers_without_sentence_are_not_steps():
    from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps
    assert _phase_bounded_numbered_steps("1\n2\n3\n") == []


def test_missing_refit_heading_does_not_invent_phase_on_page304():
    from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps
    # The original PDF page 304 left column has a second 1..7 sequence;
    # OCR may miss the separate printed Refit heading.
    lines = ["Remove"] + [
        f"{number}. Remove instruction {number}" for number in range(1, 9)
    ] + [f"{number}. Refit source instruction {number}"
         for number in range(1, 8)]
    groups = _phase_bounded_numbered_steps("\n".join(lines))
    assert [x["step_numbers"] for x in groups] == [
        list(range(1, 9)), list(range(1, 8))
    ]
    assert groups[0]["phase"] == "remove"
    assert groups[0]["sequence_status"] == "contiguous"
    assert groups[1]["phase"] == "unassigned"
    assert groups[1]["restart_without_recognized_phase"] is True
    assert groups[1]["review_required"] is True
    assert groups[1]["source_phase_heading"] is None
    assert all(not x["approved_for_technical_database"] for x in groups)


def test_unheaded_long_numbered_index_stays_uncertain_not_a_technical_procedure():
    from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps
    groups = _phase_bounded_numbered_steps(
        "1. Connector A\n2. Fuse B\n3. ECU relay\n"
    )
    assert len(groups) == 1
    assert groups[0]["phase"] == "unassigned"
    assert groups[0]["source_kind"] == "numbered_lines_unclassified"
    assert groups[0]["review_required"] is True
    assert groups[0]["review_reasons"] == [
        "numbered_lines_without_explicit_phase"
    ]
    assert all(x["validation_state"] == "OCR_NUMBERED_LINE_CANDIDATE"
               for x in groups[0]["steps"])


def test_original_headed_phase_is_procedure_candidate_not_approved():
    from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps
    groups = _phase_bounded_numbered_steps("Remove\n1. Undo bolt\n2. Lift unit")
    assert groups[0]["phase"] == "remove"
    assert groups[0]["source_kind"] == "numbered_lines_under_explicit_phase"
    assert groups[0]["review_required"] is False
    assert groups[0]["approved_for_technical_database"] is False


def test_ocr_column_segments_match_original_candidate_text():
    import pytest
    from mems_forge.ocr_strategy_audit import _phase_bounded_numbered_steps
    from mems_forge.document_pack import _numbered_column_evidence

    source_text = "Remove\n1. Undo bolt\n2. Lift unit\n1. Return unit"
    reading = {
        "text": source_text,
        "bbox_pdf": [12.0, 20.0, 260.0, 400.0],
        "phase_segments": _phase_bounded_numbered_steps(source_text),
    }
    page = {
        "pdf_page": {"width_points": 595.0, "height_points": 842.0},
        "adaptive_extraction": {"columns": {"left": reading}},
    }
    entries = _numbered_column_evidence(page, 304)
    assert len(entries) == 2
    assert entries[0][3]["phase"] == "remove"
    assert entries[1][3]["phase"] == "unassigned"
    assert entries[1][3]["review_required"]
    assert entries[0][2] == [12.0, 20.0, 260.0, 400.0]
    assert all(x["approved_for_ai"] is False
               for *_, segment in entries for x in segment["steps"])

    tampered = dict(page)
    tampered["adaptive_extraction"] = {"columns": {"left": dict(reading)}}
    tampered["adaptive_extraction"]["columns"]["left"]["phase_segments"] = (
        [{"phase": "refit", "steps": [{"number": 45, "source_text": "made up"}]}]
    )
    with pytest.raises(ValueError, match="not derived from source"):
        _numbered_column_evidence(tampered, 304)
