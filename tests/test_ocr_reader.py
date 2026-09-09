from __future__ import annotations

import unittest

from mems_forge.ocr_reader import _parse_words, _words_to_text


class OcrReaderTests(unittest.TestCase):
    def test_quote_character_does_not_swallow_following_tsv_rows(self) -> None:
        # This reproduces the failure class seen on AKM7169 page 12: a quote-like
        # OCR token must remain one physical TSV row and must never absorb the
        # rest of the page through CSV quote semantics.
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t21\t1\t1\t1\t100\t100\t80\t20\t96.0\tPETROL\n"
            "5\t1\t21\t1\t1\t2\t190\t100\t20\t20\t80.0\t\"\n"
            "5\t1\t21\t1\t1\t3\t220\t100\t100\t20\t95.0\tVAPOUR\n"
            "5\t1\t22\t1\t1\t1\t100\t160\t90\t20\t94.0\twarning\n"
        )
        words, warnings = _parse_words(tsv, 12)
        self.assertEqual(warnings, [])
        self.assertEqual([word.text for word in words], ["PETROL", '"', "VAPOUR", "warning"])
        self.assertTrue(all("\n" not in word.text for word in words))

    def test_text_is_reconstructed_deterministically_from_word_geometry(self) -> None:
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t2\t1\t1\t2\t200\t100\t50\t20\t95.0\tworld\n"
            "5\t1\t2\t1\t1\t1\t100\t100\t50\t20\t95.0\tHello\n"
            "5\t1\t3\t1\t1\t1\t100\t150\t50\t20\t95.0\tNext\n"
        )
        words, warnings = _parse_words(tsv, 1)
        self.assertEqual(warnings, [])
        self.assertEqual(_words_to_text(words), "Hello world\n\nNext")

    def test_malformed_physical_row_is_reported_not_merged(self) -> None:
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t95.0\tgood\n"
            "5\t1\tbroken\n"
            "5\t1\t1\t1\t1\t2\t50\t20\t30\t40\t95.0\trow\n"
        )
        words, warnings = _parse_words(tsv, 1)
        self.assertEqual([word.text for word in words], ["good", "row"])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "MALFORMED_TSV_ROW")


if __name__ == "__main__":
    unittest.main()
