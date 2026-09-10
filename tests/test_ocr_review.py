from __future__ import annotations

import unittest

from mems_forge.ocr_review import page_review_reasons, semantic_confidence_summary


def word(text: str, confidence: float) -> dict[str, object]:
    return {"text": text, "confidence": confidence}


class OcrReviewTests(unittest.TestCase):
    def test_punctuation_does_not_count_as_semantic_word(self) -> None:
        summary = semantic_confidence_summary(
            [word("Valve", 96.0), word("pressure", 94.0), word(".", 0.0), word("|", 0.0)]
        )
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["below_50"], 0)
        self.assertEqual(summary["mean"], 95.0)

    def test_one_doubtful_word_is_local_not_full_page_review(self) -> None:
        page = {
            "words": [word("engine", 95.0)] * 10 + [word("valve", 40.0)],
            "parse_warnings": [],
            "max_word_length": 6,
        }
        reasons, semantic = page_review_reasons(page)
        self.assertEqual(reasons, [])
        self.assertEqual(semantic["below_85"], 1)
        self.assertGreater(semantic["mean"], 85.0)

    def test_dense_doubtful_words_require_full_page_review(self) -> None:
        page = {
            "words": [word("engine", 95.0)] * 17 + [word("valve", 40.0)] * 3,
            "parse_warnings": [],
            "max_word_length": 6,
        }
        reasons, semantic = page_review_reasons(page)
        self.assertIn("dense_semantic_ocr_words_below_50", reasons)
        self.assertEqual(semantic["below_50"], 3)

    def test_empty_page_stays_review_required(self) -> None:
        reasons, semantic = page_review_reasons(
            {"words": [], "parse_warnings": [], "max_word_length": 0}
        )
        self.assertEqual(semantic["count"], 0)
        self.assertIn("no_ocr_words", reasons)

    def test_nonsemantic_ocr_only_stays_review_required(self) -> None:
        page = {
            "words": [word(".", 0.0), word("|", 0.0), word("--", 0.0)],
            "parse_warnings": [],
            "max_word_length": 2,
        }
        reasons, semantic = page_review_reasons(page)
        self.assertEqual(semantic["count"], 0)
        self.assertIn("no_semantic_ocr_words", reasons)


if __name__ == "__main__":
    unittest.main()
