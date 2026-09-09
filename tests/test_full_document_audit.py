from __future__ import annotations

import pathlib
import unittest

from mems_forge.full_document_audit import _ocr_language_for_source, _preview_pages


class FullDocumentAuditTests(unittest.TestCase):
    def test_explicit_eng_source_marker_selects_english(self) -> None:
        language, evidence = _ocr_language_for_source(pathlib.Path("AKM7169ENG-manual.pdf"))
        self.assertEqual(language, "eng")
        self.assertEqual(evidence, "filename_explicit_eng_marker")

    def test_unknown_language_is_not_silently_assumed(self) -> None:
        with self.assertRaises(RuntimeError):
            _ocr_language_for_source(pathlib.Path("manufacturer-manual.pdf"))

    def test_preview_selection_covers_start_middle_end(self) -> None:
        pages = _preview_pages(482)
        self.assertIn(1, pages)
        self.assertIn(12, pages)
        self.assertIn(241, pages)
        self.assertIn(482, pages)
        self.assertLess(len(pages), 30)


if __name__ == "__main__":
    unittest.main()
