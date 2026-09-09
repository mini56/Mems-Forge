from __future__ import annotations

import csv
import hashlib
import io
import json
import pathlib
import subprocess
from dataclasses import dataclass, asdict
from typing import Any, Iterable

import pymupdf

OCR_VERSION = "0.1.0"
DEFAULT_SAMPLE_PAGES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 100, 200, 300, 400, 482)


@dataclass(frozen=True)
class OcrWord:
    page_number: int
    block_num: int
    par_num: int
    line_num: int
    word_num: int
    left: int
    top: int
    width: int
    height: int
    confidence: float | None
    text: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_tesseract_tsv(image_path: pathlib.Path, language: str = "eng") -> tuple[str, str]:
    base = image_path.with_suffix("")
    cmd = [
        "tesseract",
        str(image_path),
        "stdout",
        "-l",
        language,
        "--psm",
        "3",
        "tsv",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)

    text_cmd = [
        "tesseract",
        str(image_path),
        "stdout",
        "-l",
        language,
        "--psm",
        "3",
    ]
    text_result = subprocess.run(text_cmd, check=True, capture_output=True, text=True)
    return result.stdout, text_result.stdout


def _parse_words(tsv: str, page_number: int) -> list[OcrWord]:
    rows = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    words: list[OcrWord] = []
    for row in rows:
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        conf_raw = row.get("conf")
        try:
            confidence = float(conf_raw) if conf_raw not in (None, "", "-1") else None
        except ValueError:
            confidence = None
        words.append(
            OcrWord(
                page_number=page_number,
                block_num=int(row.get("block_num") or 0),
                par_num=int(row.get("par_num") or 0),
                line_num=int(row.get("line_num") or 0),
                word_num=int(row.get("word_num") or 0),
                left=int(row.get("left") or 0),
                top=int(row.get("top") or 0),
                width=int(row.get("width") or 0),
                height=int(row.get("height") or 0),
                confidence=confidence,
                text=text,
            )
        )
    return words


def _confidence_summary(words: list[OcrWord]) -> dict[str, Any]:
    values = [w.confidence for w in words if w.confidence is not None]
    if not values:
        return {"count": 0, "mean": None, "below_50": 0, "below_70": 0, "below_85": 0}
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "below_50": sum(v < 50 for v in values),
        "below_70": sum(v < 70 for v in values),
        "below_85": sum(v < 85 for v in values),
    }


def _selected_pages(page_count: int, requested: Iterable[int] | None = None) -> list[int]:
    raw = tuple(requested) if requested is not None else DEFAULT_SAMPLE_PAGES
    return sorted({p for p in raw if 1 <= p <= page_count})


def run_ocr_prototype(
    pdf_path: pathlib.Path,
    output_root: pathlib.Path,
    *,
    requested_pages: Iterable[int] | None = None,
    language: str = "eng",
    dpi: int = 300,
) -> dict[str, Any]:
    pdf_path = pathlib.Path(pdf_path)
    output_root = pathlib.Path(output_root)
    doc = pymupdf.open(pdf_path)
    pages = _selected_pages(doc.page_count, requested_pages)

    out_dir = output_root / pdf_path.stem
    images_dir = out_dir / "rendered"
    pages_dir = out_dir / "pages"
    images_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    page_summaries: list[dict[str, Any]] = []
    matrix = pymupdf.Matrix(dpi / 72.0, dpi / 72.0)

    for page_number in pages:
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
        image_path = images_dir / f"page-{page_number:04d}.png"
        pix.save(image_path)
        image_bytes = image_path.read_bytes()

        tsv, text = _run_tesseract_tsv(image_path, language=language)
        words = _parse_words(tsv, page_number)
        confidence = _confidence_summary(words)

        page_payload = {
            "ocr_version": OCR_VERSION,
            "page_number": page_number,
            "language": language,
            "dpi": dpi,
            "render": {
                "width": pix.width,
                "height": pix.height,
                "sha256": _sha256_bytes(image_bytes),
            },
            "text": text,
            "words": [asdict(w) for w in words],
            "confidence": confidence,
            "review_required": bool(
                confidence["count"] == 0
                or (confidence["mean"] is not None and confidence["mean"] < 85)
                or confidence["below_50"] > 0
            ),
        }
        (pages_dir / f"page-{page_number:04d}.json").write_text(
            json.dumps(page_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        page_summaries.append(
            {
                "page_number": page_number,
                "word_count": len(words),
                "character_count": len(text),
                "confidence": confidence,
                "review_required": page_payload["review_required"],
            }
        )

    summary = {
        "ocr_version": OCR_VERSION,
        "pdf": pdf_path.as_posix(),
        "page_count": doc.page_count,
        "sample_pages": pages,
        "sample_count": len(pages),
        "language": language,
        "dpi": dpi,
        "review_required_pages": [p["page_number"] for p in page_summaries if p["review_required"]],
        "pages": page_summaries,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    doc.close()
    return summary
