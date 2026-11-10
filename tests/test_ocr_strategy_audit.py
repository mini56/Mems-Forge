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
