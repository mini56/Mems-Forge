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
