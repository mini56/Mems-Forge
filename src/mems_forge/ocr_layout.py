from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from typing import Any

LAYOUT_VERSION = "0.1.0"


def _bbox(words: list[dict[str, Any]]) -> list[int]:
    x0 = min(int(w["left"]) for w in words)
    y0 = min(int(w["top"]) for w in words)
    x1 = max(int(w["left"]) + int(w["width"]) for w in words)
    y1 = max(int(w["top"]) + int(w["height"]) for w in words)
    return [x0, y0, x1, y1]


def _block_text(words: list[dict[str, Any]]) -> str:
    by_line: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        by_line[(int(word["par_num"]), int(word["line_num"]))].append(word)
    lines: list[str] = []
    for key in sorted(by_line):
        line_words = sorted(by_line[key], key=lambda w: int(w["word_num"]))
        lines.append(" ".join(str(w["text"]) for w in line_words))
    return "\n".join(lines)


def _vertical_overlap(a: list[int], b: list[int]) -> float:
    overlap = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    denom = max(1, min(a[3] - a[1], b[3] - b[1]))
    return overlap / denom


def analyze_ocr_page(page_payload: dict[str, Any]) -> dict[str, Any]:
    render = page_payload["render"]
    width = int(render["width"])
    height = int(render["height"])
    words = page_payload.get("words", [])

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        grouped[int(word["block_num"])].append(word)

    blocks: list[dict[str, Any]] = []
    for block_num, block_words in sorted(grouped.items()):
        box = _bbox(block_words)
        center_x = (box[0] + box[2]) / 2.0
        width_ratio = (box[2] - box[0]) / max(1, width)
        blocks.append(
            {
                "block_num": block_num,
                "bbox": box,
                "center_x": center_x,
                "width_ratio": width_ratio,
                "word_count": len(block_words),
                "mean_confidence": (
                    sum(float(w["confidence"]) for w in block_words if w.get("confidence") is not None)
                    / max(1, sum(1 for w in block_words if w.get("confidence") is not None))
                ),
                "text": _block_text(block_words),
            }
        )

    # Wide blocks are treated as possible title/header/footer bands, not as columns.
    narrow = [b for b in blocks if b["word_count"] >= 2 and b["width_ratio"] < 0.68]
    left = [b for b in narrow if b["center_x"] < width * 0.47]
    right = [b for b in narrow if b["center_x"] > width * 0.53]

    overlap_pairs = []
    for lblock in left:
        for rblock in right:
            overlap = _vertical_overlap(lblock["bbox"], rblock["bbox"])
            if overlap >= 0.20:
                overlap_pairs.append((lblock["block_num"], rblock["block_num"], overlap))

    possible_two_columns = bool(left and right and overlap_pairs)

    top_band_limit = height * 0.18
    bottom_band_limit = height * 0.86
    spanning = [b for b in blocks if b["width_ratio"] >= 0.68]
    headers = [b for b in spanning if b["bbox"][1] <= top_band_limit]
    footers = [b for b in spanning if b["bbox"][1] >= bottom_band_limit]
    middle_spanning = [b for b in spanning if b not in headers and b not in footers]

    if possible_two_columns:
        candidate = (
            sorted(headers, key=lambda b: (b["bbox"][1], b["bbox"][0]))
            + sorted(left, key=lambda b: (b["bbox"][1], b["bbox"][0]))
            + sorted(right, key=lambda b: (b["bbox"][1], b["bbox"][0]))
            + sorted(middle_spanning, key=lambda b: (b["bbox"][1], b["bbox"][0]))
            + sorted(footers, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        )
        layout_type = "possible_two_columns"
        status = "REVIEW_REQUIRED"
    else:
        candidate = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        layout_type = "single_flow_or_noncolumn"
        status = "CANDIDATE_ONLY"

    # Remove duplicates if a block appeared in more than one category due to thresholds.
    seen: set[int] = set()
    ordered = []
    for block in candidate:
        if block["block_num"] in seen:
            continue
        seen.add(block["block_num"])
        ordered.append(block)
    for block in blocks:
        if block["block_num"] not in seen:
            ordered.append(block)

    candidate_text = "\n\n".join(block["text"] for block in ordered if block["text"].strip())
    return {
        "layout_version": LAYOUT_VERSION,
        "page_number": page_payload["page_number"],
        "render_width": width,
        "render_height": height,
        "layout_type": layout_type,
        "status": status,
        "possible_two_columns": possible_two_columns,
        "left_block_count": len(left),
        "right_block_count": len(right),
        "overlap_pairs": [
            {"left_block": a, "right_block": b, "vertical_overlap": overlap}
            for a, b, overlap in overlap_pairs
        ],
        "candidate_block_order": [b["block_num"] for b in ordered],
        "blocks": blocks,
        "candidate_text": candidate_text,
        "warning": "Ordre candidat fondé sur la géométrie OCR. Il n'est jamais considéré comme validé sans contrôle structurel/visuel.",
    }


def run_layout_prototype(ocr_root: pathlib.Path, output_root: pathlib.Path) -> dict[str, Any]:
    ocr_root = pathlib.Path(ocr_root)
    output_root = pathlib.Path(output_root)
    page_files = sorted(ocr_root.glob("*/pages/page-*.json"))
    if not page_files:
        raise RuntimeError(f"Aucune page OCR trouvée dans {ocr_root}")

    results = []
    for page_file in page_files:
        payload = json.loads(page_file.read_text(encoding="utf-8"))
        layout = analyze_ocr_page(payload)
        pdf_dir_name = page_file.parents[1].name
        out_dir = output_root / pdf_dir_name / "pages"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_json = out_dir / page_file.name
        out_json.write_text(json.dumps(layout, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out_dir / f"page-{layout['page_number']:04d}.txt").write_text(layout["candidate_text"] + "\n", encoding="utf-8")
        results.append(
            {
                "page_number": layout["page_number"],
                "layout_type": layout["layout_type"],
                "status": layout["status"],
                "block_count": len(layout["blocks"]),
                "left_block_count": layout["left_block_count"],
                "right_block_count": layout["right_block_count"],
            }
        )

    summary = {
        "layout_version": LAYOUT_VERSION,
        "sampled_page_count": len(results),
        "review_required_pages": [r["page_number"] for r in results if r["status"] == "REVIEW_REQUIRED"],
        "pages": results,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary
