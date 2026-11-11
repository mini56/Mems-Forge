"""Read actual-like 300 dpi Rover OCR lines without guessing hidden text."""
from __future__ import annotations

from mems_forge.procedure_fragments import analyze_page_fragments


def line(key: int, text: str, left: int, top: int, right: int, bottom: int):
    return {
        "region": "left",
        "source_line_key": [3, 1, key],
        "bbox": [left, top, right, bottom],
        "text": text,
        "word_count": len(text.split()),
        "mean_confidence": 94.0,
    }


def _page93_multiline_ocr():
    # Original Rover page 93, left column, from unmodified PSM3 OCR
    # on the source manual. Coordinates are the source raster pixels.
    lines = [
        line(1, "Refit", 51, 224, 142, 258),
        line(2, "12M0986", 364, 811, 535, 847),
        line(3, "1. Fit the timing gears without the chain.", 107, 912, 866, 957),
        line(4, "2. Check the alignment of the gears with a", 105, 962, 913, 1010),
        line(5, "Straight edge.", 161, 1015, 414, 1057),
        line(6, "3. Use feeler gauges to determine the", 105, 1064, 826, 1109),
        line(7, "thickness of shims required.", 159, 1116, 688, 1159),
        line(8, "Shim size = 0.152 mm.", 161, 1164, 614, 1201),
        line(9, "4. Remove the gears and the crankshaft", 103, 1214, 869, 1258),
        line(10, "driving key.", 160, 1265, 373, 1307),
        line(11, "5. Fit the required thickness of shims to obtain", 104, 1314, 983, 1357),
        line(12, "the correct alignment.", 158, 1365, 567, 1409),
        line(13, "6. Refit the driving key into the crankshaft.", 103, 1413, 906, 1460),
        line(14, "7. Position the crankshaft key at TDC and the", 98, 1957, 966, 2003),
        line(15, "camshaft key at 2 o’clock.", 156, 2009, 649, 2050),
        line(16, "8. Refit the timing gears and timing chain", 98, 2057, 879, 2105),
        line(17, "tighten camshaft nut to 88 Nm.", 154, 2109, 737, 2149),
        line(18, "9. Refit the timing chain tensioner, and tension", 99, 2158, 981, 2202),
        line(19, "the chain by applying firm thumb pressure", 154, 2210, 945, 2255),
        line(20, "on the tensioner and tighten the bolts to 22", 155, 2261, 965, 2304),
        line(21, "Nm.", 156, 2310, 229, 2342),
        line(22, "10. Refit oil thrower, with the 'F' mark facing", 77, 2359, 916, 2408),
        line(23, "outwards.", 154, 2411, 333, 2444),
        line(24, "11. Refit timing cover, see Timing cover oil", 76, 2459, 918, 2507),
        line(25, "seal.", 153, 2510, 245, 2543),
    ]
    return {
        "page_number": 3,
        "lines": lines,
    }


def test_page93_original_full_sentences_and_units_preserved_verbatim():
    result = analyze_page_fragments(
        _page93_multiline_ocr(), {"page_number": 3, "procedures": []}
    )
    assert result["fragment_count"] == 1
    segment = result["fragments"][0]
    steps = segment["numbered_lines"]
    assert [item["number"] for item in steps] == list(range(1, 12))
    assert steps[1]["text"] == (
        "Check the alignment of the gears with a Straight edge."
    )
    assert steps[2]["text"] == (
        "Use feeler gauges to determine the thickness of shims required. "
        "Shim size = 0.152 mm."
    )
    assert steps[7]["text"] == (
        "Refit the timing gears and timing chain tighten camshaft nut to 88 Nm."
    )
    assert steps[8]["text"] == (
        "Refit the timing chain tensioner, and tension "
        "the chain by applying firm thumb pressure "
        "on the tensioner and tighten the bolts to 22 Nm."
    )
    assert steps[10]["text"].endswith("Timing cover oil seal.")
    assert steps[2]["source_line_count"] == 3
    assert steps[8]["source_line_count"] == 4
    assert [x["source_text"] for x in steps[2]["measurement_mentions"]] == [
        "0.152 mm"
    ]
    assert [x["source_text"] for x in steps[7]["measurement_mentions"]] == [
        "88 Nm"
    ]
    assert [x["source_text"] for x in steps[8]["measurement_mentions"]] == [
        "22 Nm"
    ]
    assert all(not item["approved_for_ai"] for item in steps)
    assert any(x["text"] == "12M0986" for x in segment["unassigned_ocr_lines"])
    assert all(
        step["source_lines"][0] == step["source"] for step in steps
    )


def test_illustration_identifier_never_joins_a_step():
    layout = {
        "page_number": 3,
        "lines": [
            line(1, "Refit", 51, 200, 142, 250),
            line(2, "1. Fit cover", 100, 500, 800, 542),
            line(3, "12M0986", 152, 547, 300, 580),
            line(4, "2. Tighten bolts to 22 Nm.", 100, 595, 880, 640),
        ],
    }
    result = analyze_page_fragments(layout, {"page_number":3,"procedures":[]})
    steps = result["fragments"][0]["numbered_lines"]
    assert steps[0]["text"] == "Fit cover"
    assert steps[0]["source_line_count"] == 1
    assert "12M0986" in [
        x["text"] for x in result["fragments"][0]["unassigned_ocr_lines"]
    ]


def test_warning_kept_as_unassigned_source_not_silent_instruction():
    layout = {
        "page_number": 3,
        "lines": [
            line(1, "Refit", 51, 200, 142, 250),
            line(2, "1. Raise suspension", 100, 500, 800, 542),
            line(3, "WARNING: Support on safety stands.", 152, 547, 790, 580),
            line(4, "2. Fit damper", 100, 595, 880, 640),
        ],
    }
    fragment = analyze_page_fragments(
        layout, {"page_number":3,"procedures":[]}
    )["fragments"][0]
    assert fragment["numbered_lines"][0]["text"] == "Raise suspension"
    assert any("WARNING" in x["text"] for x in fragment["unassigned_ocr_lines"])
