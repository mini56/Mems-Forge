from __future__ import annotations

import unittest

from mems_forge.procedure_fragments import _apply_document_context


def raw_fragment(page: int, first: int, last: int, *, phase: bool = False) -> dict:
    return {
        "fragment_version": "test",
        "kind": "continuation_fragment_candidate",
        "status": "REVIEW_REQUIRED",
        "page_number": page,
        "region": "left",
        "step_numbers": list(range(first, last + 1)),
        "first_step_number": first,
        "last_step_number": last,
        "phase_markers": [{"label": "refit"}] if phase else [],
        "numbered_lines": [],
        "evidence": [],
    }


class FragmentContextTests(unittest.TestCase):
    def test_numbering_only_requires_same_chapter_and_previous_procedure_progression(self) -> None:
        raw = [
            {"page_number": 10, "fragment_count": 0, "fragments": []},
            {"page_number": 11, "fragment_count": 2, "fragments": [raw_fragment(11, 4, 6), raw_fragment(11, 20, 22)]},
        ]
        structures = {
            10: {"page_number": 10, "procedures": [{"step_numbers": [1, 2, 3]}]},
            11: {"page_number": 11, "procedures": []},
        }
        hierarchy = {
            10: {"chapter_id": "chapter-a", "chapter_title_candidate": "SOURCE A"},
            11: {"chapter_id": "chapter-a", "chapter_title_candidate": "SOURCE A"},
        }
        result = _apply_document_context(raw, structures, hierarchy)
        page11 = result[1]
        self.assertEqual(page11["raw_fragment_count"], 2)
        self.assertEqual(page11["fragment_count"], 1)
        self.assertEqual(page11["fragments"][0]["first_step_number"], 4)
        self.assertEqual(page11["rejected_raw_fragment_count"], 1)

    def test_numbering_only_never_crosses_manufacturer_chapter_boundary(self) -> None:
        raw = [
            {"page_number": 20, "fragment_count": 0, "fragments": []},
            {"page_number": 21, "fragment_count": 1, "fragments": [raw_fragment(21, 4, 6)]},
        ]
        structures = {
            20: {"page_number": 20, "procedures": [{"step_numbers": [1, 2, 3]}]},
            21: {"page_number": 21, "procedures": []},
        }
        hierarchy = {
            20: {"chapter_id": "chapter-a", "chapter_title_candidate": "A"},
            21: {"chapter_id": "chapter-b", "chapter_title_candidate": "B"},
        }
        result = _apply_document_context(raw, structures, hierarchy)
        self.assertEqual(result[1]["fragment_count"], 0)
        self.assertEqual(
            result[1]["rejected_raw_fragments"][0]["reason"],
            "numbering_only_without_same_resolved_chapter",
        )

    def test_source_phase_heading_remains_review_candidate(self) -> None:
        raw = [
            {"page_number": 30, "fragment_count": 1, "fragments": [raw_fragment(30, 1, 3, phase=True)]},
        ]
        result = _apply_document_context(
            raw,
            {30: {"page_number": 30, "procedures": []}},
            {30: {"chapter_id": "chapter-a", "chapter_title_candidate": "A"}},
        )
        self.assertEqual(result[0]["fragment_count"], 1)
        self.assertIn("manufacturer_phase_heading", result[0]["fragments"][0]["context_evidence"])


if __name__ == "__main__":
    unittest.main()
