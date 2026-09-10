from __future__ import annotations

import unittest

from mems_forge.document_hierarchy import build_document_hierarchy


def line(region: str, key: int, text: str) -> dict:
    return {
        "region": region,
        "source_line_key": [key, 1, 1],
        "bbox": [100, key * 30, 900, key * 30 + 20],
        "text": text,
        "word_count": max(1, len(text.split())),
        "mean_confidence": 96.0,
    }


def layout(page_number: int, lines: list[dict]) -> dict:
    return {"page_number": page_number, "lines": lines}


def structure(page_number: int, page_class: str = "unclassified_candidate") -> dict:
    return {"page_number": page_number, "page_class": page_class, "procedures": []}


class DocumentHierarchyTests(unittest.TestCase):
    def test_contents_anchor_uses_following_manufacturer_header_without_hardcoded_domain(self) -> None:
        layouts = [
            layout(1, []),
            layout(2, [line("body", 1, "CONTENTS"), line("body", 2, "Pump overhaul 3")]),
            layout(3, [line("header", 1, "HYDRAULIC TEST ZONE"), line("body", 2, "Pump")]),
            layout(4, [line("body", 1, "Procedure")]),
            layout(5, []),
            layout(6, [line("header", 1, "SECOND SOURCE CHAPTER"), line("body", 2, "CONTENTS")]),
            layout(7, []),
        ]
        structures = [
            structure(1),
            structure(2, "contents_page_candidate"),
            structure(3),
            structure(4),
            structure(5),
            structure(6, "contents_page_candidate"),
            structure(7),
        ]
        result = build_document_hierarchy(layouts, structures)
        self.assertEqual(result["chapter_candidate_count"], 2)
        first = result["chapters"][0]
        self.assertEqual(first["title_candidate"], "HYDRAULIC TEST ZONE")
        self.assertEqual(first["start_physical_page"], 2)
        self.assertEqual(first["end_physical_page"], 5)
        self.assertEqual(first["anchor_evidence"][0]["title_detection_method"], "nearby_following_page_header")
        self.assertEqual(first["anchor_evidence"][0]["title_evidence"]["physical_page_number"], 3)

    def test_repeated_contents_pages_with_same_source_title_form_one_chapter(self) -> None:
        layouts = [
            layout(1, [line("header", 1, "BODY SOURCE LABEL"), line("body", 2, "CONTENTS")]),
            layout(2, []),
            layout(3, [line("body", 1, "CONTENTS")]),
            layout(4, [line("header", 1, "BODY SOURCE LABEL"), line("body", 2, "Door")]),
            layout(5, []),
        ]
        structures = [
            structure(1, "contents_page_candidate"),
            structure(2),
            structure(3, "contents_page_candidate"),
            structure(4),
            structure(5),
        ]
        result = build_document_hierarchy(layouts, structures)
        self.assertEqual(result["chapter_candidate_count"], 1)
        self.assertEqual(result["chapters"][0]["contents_pages"], [1, 3])
        self.assertEqual(result["chapters"][0]["end_physical_page"], 5)

    def test_contents_entries_keep_raw_text_bbox_and_printed_page_candidate(self) -> None:
        layouts = [
            layout(
                1,
                [
                    line("header", 1, "SOURCE CHAPTER"),
                    line("body", 2, "CONTENTS"),
                    line("body", 3, "REPAIRS"),
                    line("body", 4, "Brake caliper overhaul ........ 12"),
                ],
            )
        ]
        structures = [structure(1, "contents_page_candidate")]
        result = build_document_hierarchy(layouts, structures)
        entries = result["anchors"][0]["contents_entries"]
        item = next(entry for entry in entries if entry["role"] == "contents_entry_candidate")
        self.assertEqual(item["raw_text"], "Brake caliper overhaul ........ 12")
        self.assertEqual(item["printed_page_candidate"], 12)
        self.assertEqual(item["source"]["physical_page_number"], 1)
        self.assertEqual(item["source"]["bbox"], [100, 120, 900, 140])


if __name__ == "__main__":
    unittest.main()
