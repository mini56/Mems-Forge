from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import pymupdf

from mems_forge.pdf_reader import analyze_pdf


class PdfReaderTests(unittest.TestCase):
    def test_native_text_page_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            pdf = root / "text.pdf"
            doc = pymupdf.open()
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 100), "MEMS Forge test source")
            page.insert_text((72, 140), "Step-like value 12.5 V")
            doc.save(pdf)
            doc.close()

            summary = analyze_pdf(pdf, root / "out")
            self.assertEqual(summary["counts"]["pages"], 1)
            self.assertEqual(summary["counts"]["native_text_pages"], 1)
            self.assertGreater(summary["counts"]["raw_spans"], 0)

            source_dir = root / "out" / summary["source"]["sha256"]
            source = json.loads((source_dir / "source.json").read_text(encoding="utf-8"))
            self.assertEqual(source["page_count"], 1)
            self.assertTrue((source_dir / "raw_spans.jsonl.gz").exists())

    def test_blank_page_requires_render_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            pdf = root / "blank.pdf"
            doc = pymupdf.open()
            doc.new_page(width=595, height=842)
            doc.save(pdf)
            doc.close()

            summary = analyze_pdf(pdf, root / "out")
            self.assertEqual(summary["counts"]["pages"], 1)
            self.assertEqual(summary["counts"]["blank_verified_pages"], 1)


if __name__ == "__main__":
    unittest.main()
