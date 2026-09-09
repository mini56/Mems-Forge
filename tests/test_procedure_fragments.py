from __future__ import annotations

import unittest

from mems_forge.procedure_fragments import analyze_page_fragments


def line(region: str, key: int, text: str) -> dict:
    return {
        "region": region,
        "source_line_key": [key, 1, 1],
        "bbox": [100, key * 30, 900, key * 30 + 20],
        "text": text,
        "word_count": max(1, len(text.split())),
        "mean_confidence": 95.0,
    }


class ProcedureFragmentTests(unittest.TestCase):
    def test_left_column_continuation_can_coexist_with_new_right_procedure(self) -> None:
        layout = {
            "page_number": 54,
            "lines": [
                line("left", 1, "9. Continue bleeding sequence"),
                line("left", 2, "10. Close bleed screw"),
                line("left", 3, "11. Refill reservoir"),
                line("left", 4, "12. Check pedal"),
                line("right", 5, "HANDBRAKE CABLE ADJUST"),
                line("right", 6, "Service Repair No. 70.35.10"),
                line("right", 7, "Adjust"),
                line("right", 8, "1. Raise vehicle"),
                line("right", 9, "2. Adjust cable"),
            ],
        }
        structure = {
            "page_number": 54,
            "procedures": [{"region": "right", "repair_number": "70.35.10"}],
        }
        result = analyze_page_fragments(layout, structure)
        self.assertEqual(result["fragment_count"], 1)
        fragment = result["fragments"][0]
        self.assertEqual(fragment["region"], "left")
        self.assertEqual(fragment["step_numbers"], [9, 10, 11, 12])
        self.assertIn("numbering_starts_after_one", fragment["evidence"])

    def test_refit_phase_without_local_repair_number_is_continuation_candidate(self) -> None:
        layout = {
            "page_number": 95,
            "lines": [
                line("left", 1, "Refit"),
                line("left", 2, "1. Fit bearing shell"),
                line("left", 3, "2. Fit connecting rod"),
                line("left", 4, "3. Tighten nuts"),
                line("left", 5, "4. Check rotation"),
            ],
        }
        result = analyze_page_fragments(layout, {"page_number": 95, "procedures": []})
        self.assertEqual(result["fragment_count"], 1)
        self.assertEqual(result["fragments"][0]["phase_markers"][0]["label"], "refit")

    def test_component_list_starting_at_one_is_not_continuation_fragment(self) -> None:
        layout = {
            "page_number": 300,
            "lines": [line("left", i, f"{i}. Component {i}") for i in range(1, 20)],
        }
        result = analyze_page_fragments(layout, {"page_number": 300, "procedures": []})
        self.assertEqual(result["fragment_count"], 0)

    def test_region_with_local_repair_number_is_not_fragment(self) -> None:
        layout = {
            "page_number": 200,
            "lines": [
                line("left", 1, "Service Repair No. 37.25.05"),
                line("left", 2, "Remove"),
                line("left", 3, "1. First"),
                line("left", 4, "2. Second"),
            ],
        }
        structure = {
            "page_number": 200,
            "procedures": [{"region": "left", "repair_number": "37.25.05"}],
        }
        result = analyze_page_fragments(layout, structure)
        self.assertEqual(result["fragment_count"], 0)


if __name__ == "__main__":
    unittest.main()
