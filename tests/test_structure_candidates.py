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
        self.assertTrue(all(p["step_sequence_status"] == "contiguous_by_phase" for p in result["procedures"]))

    def test_manufacturer_numbering_restart_between_remove_and_refit_is_clean(self) -> None:
        payload = {
            "page_number": 90,
            "lines": [
                line("left", 1, "MAIN BEARING CAP OIL SEAL"),
                line("left", 2, "Service Repair No. 12.21.20"),
                line("left", 3, "Remove"),
                line("left", 4, "1. Remove flywheel"),
                line("left", 5, "2. Remove oil seal"),
                line("left", 6, "Refit"),
                line("left", 7, "1. Clean seal location"),
                line("left", 8, "2. Fit new seal"),
                line("left", 9, "3. Lubricate seal lip"),
                line("left", 10, "4. Refit flywheel"),
            ],
        }
        result = analyze_structure_candidates(payload)
        proc = result["procedures"][0]
        self.assertEqual(proc["step_numbers"], [1, 2, 1, 2, 3, 4])
        self.assertEqual(proc["step_sequence_status"], "contiguous_by_phase")
        self.assertNotIn("phase_step_sequence_not_contiguous", proc["review_reasons"])
        self.assertEqual(proc["phases"][0]["step_sequence_status"], "contiguous")
        self.assertEqual(proc["phases"][1]["step_sequence_status"], "contiguous")

    def test_refitting_exact_manufacturer_heading_maps_to_refit(self) -> None:
        payload = {
            "page_number": 373,
            "lines": [
                line("body", 1, "SUN ROOF FRONT SEAL"),
                line("body", 2, "Service Repair No. 76.82.65"),
                line("body", 3, "Remove"),
                line("body", 4, "1. Open sun roof"),
                line("body", 5, "2. Release front seal"),
                line("body", 6, "3. Remove seal"),
                line("body", 7, "Refitting"),
                line("body", 8, "1. Clean mounting face"),
                line("body", 9, "2. Position seal"),
                line("body", 10, "3. Press seal into place"),
                line("body", 11, "4. Check alignment"),
                line("body", 12, "5. Close sun roof"),
            ],
        }
        result = analyze_structure_candidates(payload)
        proc = result["procedures"][0]
        self.assertEqual([phase["label"] for phase in proc["phases"]], ["remove", "refit"])
        self.assertEqual(proc["phases"][1]["source_label"], "refitting")
        self.assertEqual(proc["phases"][1]["step_numbers"], [1, 2, 3, 4, 5])
        self.assertEqual(proc["step_sequence_status"], "contiguous_by_phase")

    def test_ocr_typo_phase_is_not_silently_normalised(self) -> None:
        payload = {
            "page_number": 120,
            "lines": [
                line("body", 1, "TEST UNIT"),
                line("body", 2, "Service Repair No. 12.34.56"),
                line("body", 3, "Dismantie"),
                line("body", 4, "1. First item"),
                line("body", 5, "2. Second item"),
            ],
        }
        result = analyze_structure_candidates(payload)
        self.assertEqual(result["procedure_count"], 0)

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

    def test_noncontiguous_steps_inside_one_phase_are_flagged(self) -> None:
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
        self.assertEqual(proc["step_sequence_status"], "phase_sequence_issue")
        self.assertIn("phase_step_sequence_not_contiguous", proc["review_reasons"])
        self.assertEqual(proc["phases"][0]["step_sequence_status"], "gap_restart_or_ocr_error")

    def test_contents_word_inside_prose_does_not_classify_contents_page(self) -> None:
        lines = [
            line("left", 1, "GENERAL INFORMATION"),
            line("left", 2, "FUEL HANDLING PRECAUTIONS"),
            line("left", 3, "The receptacle should be clearly marked with its contents, and placed in a safe storage area."),
            line("right", 4, "Fuel tank removal"),
            line("right", 5, "Fuel tank repairs"),
            line("right", 6, "Body and chassis repairs"),
            line("right", 7, "Petrol vapour is highly flammable."),
            line("right", 8, "Fuel lines must not be removed whilst the vehicle is over an inspection pit."),
        ]
        result = analyze_structure_candidates({"page_number": 12, "lines": lines})
        self.assertNotEqual(result["page_class"], "contents_page_candidate")

    def test_standalone_contents_heading_can_classify_contents_page(self) -> None:
        lines = [line("body", 1, "CONTENTS")]
        lines.extend(line("body", i + 2, f"Section {i} ........ {i}") for i in range(1, 9))
        result = analyze_structure_candidates({"page_number": 3, "lines": lines})
        self.assertEqual(result["page_class"], "contents_page_candidate")


if __name__ == "__main__":
    unittest.main()
