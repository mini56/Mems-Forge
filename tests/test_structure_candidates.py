from __future__ import annotations

import unittest

from mems_forge.structure_candidates import analyze_structure_candidates


def line(region: str, key: int, text: str, confidence: float = 95.0) -> dict:
    return {
        "region": region,
        "source_line_key": [key, 1, 1],
        "bbox": [100, key * 40, 900, key * 40 + 30],
        "text": text,
        "word_count": max(1, len(text.split())),
        "mean_confidence": confidence,
    }


class StructureCandidateTests(unittest.TestCase):
    def test_two_columns_can_produce_two_independent_procedures(self) -> None:
        payload = {
            "page_number": 200,
            "lines": [
                line("left", 1, "SPEEDOMETER DRIVE GEAR PINION"),
                line("left", 2, "Service Repair No. 37.25.05"),
                line("left", 3, "Remove"),
                line("left", 4, "1. Disconnect speedometer drive cable"),
                line("left", 5, "2. Remove retaining plate"),
                line("left", 6, "3. Remove pinion housing assembly"),
                line("left", 7, "4. Pull out speedometer drive pinion"),
                line("left", 8, "Refit"),
                line("left", 9, "5. Reverse procedures in 1 to 4"),
                line("right", 10, "SPEEDOMETER DRIVE HOUSING"),
                line("right", 11, "Service Repair No. 37.25.09"),
                line("right", 12, "Remove"),
                line("right", 13, "1. Remove engine/gearbox assembly"),
                line("right", 14, "2. Remove radiator bolts"),
                line("right", 15, "3. Remove engine mounting bracket"),
                line("right", 16, "4. Remove adapter plate"),
                line("right", 17, "5. Withdraw drive pinion assembly"),
                line("right", 18, "6. Withdraw housing"),
                line("right", 19, "7. Remove housing joint washer"),
                line("right", 20, "Refit"),
                line("right", 21, "8. Reverse procedure in 2 to 7"),
                line("right", 22, "9. Refit engine/gearbox assembly"),
            ],
        }
        result = analyze_structure_candidates(payload)
        self.assertEqual(result["page_class"], "repair_procedure_page")
        self.assertEqual(result["procedure_count"], 2)
        self.assertEqual([p["repair_number"] for p in result["procedures"]], ["37.25.05", "37.25.09"])
        self.assertEqual(result["procedures"][0]["step_numbers"], [1, 2, 3, 4, 5])
        self.assertEqual(result["procedures"][1]["step_numbers"], list(range(1, 10)))
        self.assertTrue(all(p["step_sequence_status"] == "contiguous" for p in result["procedures"]))

    def test_numbered_component_list_is_not_promoted_to_procedure(self) -> None:
        lines = [line("body", 1, "FRONT SUSPENSION COMPONENTS")]
        lines.extend(line("body", i + 2, f"{i}. Component {i}") for i in range(1, 15))
        result = analyze_structure_candidates({"page_number": 300, "lines": lines})
        self.assertEqual(result["procedure_count"], 0)
        self.assertEqual(result["page_class"], "numbered_component_or_reference_list_candidate")
        self.assertEqual(result["numbered_line_count"], 14)

    def test_phase_word_without_repair_context_does_not_create_procedure(self) -> None:
        payload = {
            "page_number": 50,
            "lines": [
                line("body", 1, "GENERAL INFORMATION"),
                line("body", 2, "Remove"),
                line("body", 3, "1. This number belongs to an ordinary list"),
                line("body", 4, "2. Another numbered item"),
            ],
        }
        result = analyze_structure_candidates(payload)
        self.assertEqual(result["procedure_count"], 0)

    def test_noncontiguous_steps_are_flagged_for_review(self) -> None:
        payload = {
            "page_number": 99,
            "lines": [
                line("body", 1, "TEST PROCEDURE"),
                line("body", 2, "Service Repair No. 12.34.56"),
                line("body", 3, "Remove"),
                line("body", 4, "1. First step"),
                line("body", 5, "3. Third step after OCR loss"),
            ],
        }
        result = analyze_structure_candidates(payload)
        self.assertEqual(result["procedure_count"], 1)
        proc = result["procedures"][0]
        self.assertEqual(proc["step_sequence_status"], "gap_restart_or_ocr_error")
        self.assertIn("step_sequence_not_contiguous", proc["review_reasons"])


if __name__ == "__main__":
    unittest.main()
