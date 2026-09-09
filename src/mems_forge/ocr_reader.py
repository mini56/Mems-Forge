from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import subprocess
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Any, Iterable

import pymupdf

OCR_VERSION = "0.2.0"
DEFAULT_SAMPLE_PAGES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 100, 200, 300, 400, 482)
TSV_COLUMNS = (
    "level",
    "page_num",
    "block_num",
    "par_num",
    "line_num",
    "word_num",
    "left",
    "top",
    "width",
    "height",
    "conf",
    "text",
)


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
    """Run Tesseract once and return its raw TSV plus stderr.

    The TSV is the immutable OCR extraction for this prototype. Human-readable
    text is reconstructed deterministically from its word geometry instead of
    launching a second OCR pass that could disagree with the first one.
    """
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
    return result.stdout, result.stderr


def _parse_words(tsv: str, page_number: int) -> tuple[list[OcrWord], list[dict[str, Any]]]:
    """Parse Tesseract TSV without CSV quote semantics.

    Tesseract text can legitimately contain quote characters. ``csv.DictReader``
    may interpret those as field quoting and accidentally swallow many physical
    TSV lines into one word. We therefore split each physical line into exactly
    12 tab-separated fields and never allow a word token to consume another row.
    """
    physical_lines = tsv.splitlines()
    warnings: list[dict[str, Any]] = []
    words: list[OcrWord] = []

    if not physical_lines:
        return words, [{"code": "EMPTY_TSV", "line_number": 0}]

    header = tuple(physical_lines[0].split("\t"))
    if header != TSV_COLUMNS:
        warnings.append(
            {
                "code": "UNEXPECTED_TSV_HEADER",
                "line_number": 1,
                "expected": list(TSV_COLUMNS),
                "actual": list(header),
            }
        )

    for line_number, raw_line in enumerate(physical_lines[1:], start=2):
        # maxsplit=11 preserves any additional tab characters inside the last
        # field as part of that row only. They can never merge subsequent rows.
        fields = raw_line.split("\t", 11)
        if len(fields) != 12:
            warnings.append(
                {
                    "code": "MALFORMED_TSV_ROW",
                    "line_number": line_number,
                    "field_count": len(fields),
                    "sha256": _sha256_bytes(raw_line.encode("utf-8", errors="replace")),
                }
            )
            continue

        if fields[0] != "5":
            continue

        text = fields[11].strip()
        if not text:
            continue

        try:
            confidence = None if fields[10] in ("", "-1") else float(fields[10])
            word = OcrWord(
                page_number=page_number,
                block_num=int(fields[2] or 0),
                par_num=int(fields[3] or 0),
                line_num=int(fields[4] or 0),
                word_num=int(fields[5] or 0),
                left=int(fields[6] or 0),
                top=int(fields[7] or 0),
                width=int(fields[8] or 0),
                height=int(fields[9] or 0),
                confidence=confidence,
                text=text,
            )
        except (TypeError, ValueError) as exc:
            warnings.append(
                {
                    "code": "INVALID_TSV_WORD_ROW",
                    "line_number": line_number,
                    "error": str(exc),
                    "sha256": _sha256_bytes(raw_line.encode("utf-8", errors="replace")),
                }
            )
            continue

        if "\n" in word.text or "\r" in word.text:
            warnings.append(
                {
                    "code": "WORD_CONTAINS_LINE_BREAK",
                    "line_number": line_number,
                    "text_sha256": _sha256_bytes(word.text.encode("utf-8", errors="replace")),
                }
            )
            continue

        words.append(word)

    return words, warnings


def _words_to_text(words: list[OcrWord]) -> str:
    """Deterministic plain text reconstructed from the immutable TSV words."""
    grouped: dict[tuple[int, int, int], list[OcrWord]] = defaultdict(list)
    for word in words:
        grouped[(word.block_num, word.par_num, word.line_num)].append(word)

    lines: list[str] = []
    previous_block_par: tuple[int, int] | None = None
    for key in sorted(grouped):
        block_par = key[:2]
        if previous_block_par is not None and block_par != previous_block_par and lines and lines[-1] != "":
            lines.append("")
        line_words = sorted(grouped[key], key=lambda item: item.word_num)
        lines.append(" ".join(item.text for item in line_words))
        previous_block_par = block_par
    return "\n".join(lines).strip()


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
    raw_dir = out_dir / "raw"
    images_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    page_summaries: list[dict[str, Any]] = []
    matrix = pymupdf.Matrix(dpi / 72.0, dpi / 72.0)

    for page_number in pages:
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
        image_path = images_dir / f"page-{page_number:04d}.png"
        pix.save(image_path)
        image_bytes = image_path.read_bytes()

        tsv, stderr = _run_tesseract_tsv(image_path, language=language)
        raw_tsv = tsv.encode("utf-8")
        raw_path = raw_dir / f"page-{page_number:04d}.tsv.gz"
        with gzip.open(raw_path, "wb") as stream:
            stream.write(raw_tsv)

        words, parse_warnings = _parse_words(tsv, page_number)
        text = _words_to_text(words)
        confidence = _confidence_summary(words)

        review_required = bool(
            parse_warnings
            or confidence["count"] == 0
            or (confidence["mean"] is not None and confidence["mean"] < 85)
            or confidence["below_50"] > 0
        )

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
            "raw_ocr": {
                "format": "tesseract_tsv",
                "sha256": _sha256_bytes(raw_tsv),
                "gzip_file": raw_path.name,
                "stderr": stderr.strip(),
            },
            "text": text,
            "text_source": "deterministic_reconstruction_from_tsv_words",
            "words": [asdict(w) for w in words],
            "confidence": confidence,
            "parse_warnings": parse_warnings,
            "review_required": review_required,
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
                "parse_warning_count": len(parse_warnings),
                "review_required": review_required,
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
        "parse_warning_pages": [p["page_number"] for p in page_summaries if p["parse_warning_count"]],
        "pages": page_summaries,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    doc.close()
    return summary
