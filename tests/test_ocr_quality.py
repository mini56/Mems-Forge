from __future__ import annotations

import unittest

from mems_forge.ocr_quality import audit_document_ocr_spelling, build_document_lexicon, propose_spelling_candidates


def word(text: str, confidence: float, *, line: int = 1, n: int = 1) -> dict:
    return {
        "text": text,
        "confidence": confidence,
        "left": 10 * n,
        "top": 20 * line,
        "width": max(10, len(text) * 6),
        "height": 15,
        "block_num": 1,
        "par_num": 1,
        "line_num": line,
        "word_num": n,
    }


class OcrQualityTests(unittest.TestCase):
    def test_repeated_high_confidence_source_word_can_propose_correction(self) -> None:
        pages = [
            {"page_number": 1, "words": [word("Dismantle", 98.0)]},
            {"page_number": 2, "words": [word("dismantle", 97.0)]},
            {"page_number": 3, "words": [word("Dismantie", 41.0)]},
        ]
        lexicon = build_document_lexicon(pages)
        candidates = propose_spelling_candidates(pages[2], lexicon)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["original"], "Dismantie")
        self.assertEqual(candidates[0]["proposals"][0]["normalised"], "dismantle")
        self.assertFalse(candidates[0]["applied"])
        self.assertIn("REVIEW_REQUIRED", candidates[0]["status"])

    def test_raw_ocr_is_never_replaced(self) -> None:
        page = {"page_number": 1, "words": [word("Refiting", 35.0)]}
        reference = [
            page,
            {"page_number": 2, "words": [word("Refitting", 98.0)]},
            {"page_number": 3, "words": [word("Refitting", 99.0)]},
        ]
        before = page["words"][0]["text"]
        result = audit_document_ocr_spelling(reference)
        self.assertEqual(page["words"][0]["text"], before)
        self.assertEqual(result["pages"][0]["translation_gate"], "BLOCKED_OCR_REVIEW")

    def test_high_confidence_word_does_not_need_spelling_candidate(self) -> None:
        pages = [
            {"page_number": 1, "words": [word("gearbox", 98.0)]},
            {"page_number": 2, "words": [word("gearbox", 99.0)]},
            {"page_number": 3, "words": [word("gearbox", 96.0)]},
        ]
        result = audit_document_ocr_spelling(pages)
        self.assertEqual(result["spelling_candidate_count"], 0)
        self.assertEqual(result["pages_blocked_for_translation"], [])


if __name__ == "__main__":
    unittest.main()
