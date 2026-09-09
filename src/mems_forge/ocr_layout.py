from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from typing import Any

LAYOUT_VERSION = "0.2.0"
HEADER_LIMIT = 0.055
FOOTER_LIMIT = 0.94


def _line_records(words: list[dict[str, Any]], *, region: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        grouped[(int(word["block_num"]), int(word["par_num"]), int(word["line_num"]))].append(word)

    lines = []
    for key, line_words in grouped.items():
        line_words.sort(key=lambda w: int(w["left"]))
        x0 = min(int(w["left"]) for w in line_words)
        y0 = min(int(w["top"]) for w in line_words)
        x1 = max(int(w["left"]) + int(w["width"]) for w in line_words)
        y1 = max(int(w["top"]) + int(w["height"]) for w in line_words)
        conf = [float(w["confidence"]) for w in line_words if w.get("confidence") is not None]
        lines.append(
            {
                "region": region,
                "source_line_key": [key[0], key[1], key[2]],
                "bbox": [x0, y0, x1, y1],
                "text": " ".join(str(w["text"]) for w in line_words),
                "word_count": len(line_words),
                "mean_confidence": sum(conf) / len(conf) if conf else None,
            }
        )
    return sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0]))


def _best_gutter(words: list[dict[str, Any]], width: int) -> dict[str, Any] | None:
    """Find a plausible vertical gutter using word geometry, not OCR text order.

    The detector deliberately returns a *candidate*. It does not validate a page as
    two-column; it only says that left and right text populations are separated by a
    sufficiently clean vertical gap.
    """
    if len(words) < 20 or width <= 0:
        return None

    best = None
    for x in range(int(width * 0.32), int(width * 0.68) + 1, 10):
        crossing = [w for w in words if int(w["left"]) < x < int(w["left"]) + int(w["width"])]
        left = [w for w in words if int(w["left"]) + int(w["width"]) <= x]
        right = [w for w in words if int(w["left"]) >= x]
        if len(left) < 10 or len(right) < 10:
            continue

        left_edge = max((int(w["left"]) + int(w["width"]) for w in left), default=x)
        right_edge = min((int(w["left"]) for w in right), default=x)
        gap = right_edge - left_edge
        balance = min(len(left), len(right)) / max(len(left), len(right))

        # Crossing words are heavily penalised. A real central whitespace gutter is
        # rewarded, as is a reasonable balance between left and right populations.
        score = len(crossing) * 1000 - max(0, gap) * 2 - balance * 20
        candidate = {
            "x": x,
            "crossing_word_count": len(crossing),
            "left_word_count": len(left),
            "right_word_count": len(right),
            "gap_px": gap,
            "balance": balance,
            "score": score,
        }
        if best is None or score < best["score"]:
            best = candidate

    if best is None:
        return None

    # Strong balanced gutter, or a wider but somewhat unbalanced gutter.
    plausible = (
        best["crossing_word_count"] <= 1
        and (
            (best["balance"] >= 0.35 and best["gap_px"] >= 35)
            or (best["balance"] >= 0.18 and best["gap_px"] >= 120)
        )
    )
    best["plausible_two_columns"] = plausible
    return best


def analyze_ocr_page(page_payload: dict[str, Any]) -> dict[str, Any]:
    width = int(page_payload["render"]["width"])
    height = int(page_payload["render"]["height"])
    words = list(page_payload.get("words", []))

    header_words = [w for w in words if int(w["top"]) < height * HEADER_LIMIT]
    footer_words = [w for w in words if int(w["top"]) >= height * FOOTER_LIMIT]
    body_words = [w for w in words if w not in header_words and w not in footer_words]

    gutter = _best_gutter(body_words, width)
    possible_two_columns = bool(gutter and gutter["plausible_two_columns"])

    regions: dict[str, list[dict[str, Any]]] = {
        "header": header_words,
        "left": [],
        "right": [],
        "spanning": [],
        "body": [],
        "footer": footer_words,
    }

    if possible_two_columns:
        gx = int(gutter["x"])
        for word in body_words:
            left = int(word["left"])
            right = left + int(word["width"])
            if right <= gx:
                regions["left"].append(word)
            elif left >= gx:
                regions["right"].append(word)
            else:
                regions["spanning"].append(word)
        region_order = ["header", "left", "right", "spanning", "footer"]
        layout_type = "possible_two_columns"
        status = "REVIEW_REQUIRED"
    else:
        regions["body"] = body_words
        region_order = ["header", "body", "footer"]
        layout_type = "single_flow_or_noncolumn"
        status = "CANDIDATE_ONLY"

    lines_by_region = {region: _line_records(regions[region], region=region) for region in region_order}
    ordered_lines = [line for region in region_order for line in lines_by_region[region]]
    candidate_text = "\n".join(line["text"] for line in ordered_lines if line["text"].strip())

    return {
        "layout_version": LAYOUT_VERSION,
        "page_number": page_payload["page_number"],
        "render_width": width,
        "render_height": height,
        "layout_type": layout_type,
        "status": status,
        "possible_two_columns": possible_two_columns,
        "gutter": gutter,
        "region_order": region_order,
        "region_word_counts": {region: len(regions[region]) for region in region_order},
        "lines": ordered_lines,
        "candidate_text": candidate_text,
        "warning": "Ordre candidat fondé sur la géométrie des mots OCR. Il reste non publié tant que structure, titres, procédures, tableaux et visuels ne sont pas validés.",
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
        (out_dir / page_file.name).write_text(json.dumps(layout, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out_dir / f"page-{layout['page_number']:04d}.txt").write_text(layout["candidate_text"] + "\n", encoding="utf-8")
        results.append(
            {
                "page_number": layout["page_number"],
                "layout_type": layout["layout_type"],
                "status": layout["status"],
                "gutter": layout["gutter"],
                "region_word_counts": layout["region_word_counts"],
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
