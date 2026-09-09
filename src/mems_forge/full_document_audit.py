from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict
from typing import Any

import pymupdf

from mems_forge.ocr_layout import run_layout_prototype
from mems_forge.ocr_reader import (
    OCR_VERSION,
    _confidence_summary,
    _parse_words,
    _run_tesseract_tsv,
    _words_to_text,
)
from mems_forge.structure_candidates import run_structure_prototype

FULL_AUDIT_VERSION = "0.1.0"
DEFAULT_DPI = 300


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ocr_language_for_source(pdf_path: pathlib.Path) -> tuple[str, str]:
    """Choose OCR language only when the source filename provides explicit evidence.

    The first real MEMS Forge stress-test document is AKM7169ENG. We deliberately
    refuse to silently assume English for future documents whose language is not
    identified by the source name. A later document classifier can replace this
    conservative bootstrap rule.
    """
    upper_name = pdf_path.name.upper()
    if "ENG" in upper_name:
        return "eng", "filename_explicit_eng_marker"
    raise RuntimeError(
        f"Langue OCR non déterminée pour {pdf_path.name}. "
        "Le full audit refuse une hypothèse silencieuse."
    )


def _preview_pages(page_count: int) -> set[int]:
    pages = set(range(1, min(page_count, 12) + 1))
    pages.update({100, 200, 300, 400, page_count})
    # Add coarse coverage through the document without retaining 482 large renders.
    if page_count > 20:
        for ratio in (0.1, 0.25, 0.5, 0.75, 0.9):
            pages.add(max(1, min(page_count, round(page_count * ratio))))
    return {page for page in pages if 1 <= page <= page_count}


def _run_full_ocr(
    pdf_path: pathlib.Path,
    output_root: pathlib.Path,
    *,
    dpi: int = DEFAULT_DPI,
) -> dict[str, Any]:
    source_sha = _sha256_file(pdf_path)
    language, language_evidence = _ocr_language_for_source(pdf_path)
    doc = pymupdf.open(pdf_path)
    if doc.needs_pass:
        raise RuntimeError(f"PDF protégé par mot de passe: {pdf_path}")

    source_dir = output_root / source_sha
    pages_dir = source_dir / "pages"
    raw_dir = source_dir / "raw"
    previews_dir = source_dir / "previews"
    pages_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)

    preview_pages = _preview_pages(doc.page_count)
    scale = dpi / 72.0
    matrix = pymupdf.Matrix(scale, scale)
    page_summaries: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="mems-forge-ocr-") as tmp:
        tmp_dir = pathlib.Path(tmp)
        for page_number in range(1, doc.page_count + 1):
            page = doc.load_page(page_number - 1)
            pix = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
            temp_image = tmp_dir / f"page-{page_number:04d}.png"
            pix.save(temp_image)
            image_bytes = temp_image.read_bytes()
            render_sha = _sha256_bytes(image_bytes)

            if page_number in preview_pages:
                shutil.copyfile(temp_image, previews_dir / temp_image.name)

            tsv, stderr = _run_tesseract_tsv(temp_image, language=language)
            raw_tsv = tsv.encode("utf-8")
            raw_path = raw_dir / f"page-{page_number:04d}.tsv.gz"
            with gzip.open(raw_path, "wb") as stream:
                stream.write(raw_tsv)

            words, parse_warnings = _parse_words(tsv, page_number)
            text = _words_to_text(words)
            confidence = _confidence_summary(words)
            max_word_length = max((len(word.text) for word in words), default=0)

            review_reasons: list[str] = []
            if parse_warnings:
                review_reasons.append("ocr_parse_warning")
            if not words:
                review_reasons.append("no_ocr_words")
            if confidence["mean"] is not None and confidence["mean"] < 85:
                review_reasons.append("mean_ocr_confidence_below_85")
            if confidence["below_50"] > 0:
                review_reasons.append("contains_ocr_word_below_50")
            if max_word_length > 120:
                # A very long OCR token is suspicious and specifically protects
                # against the historical TSV-row swallowing corruption.
                review_reasons.append("suspiciously_long_ocr_token")

            page_payload = {
                "full_audit_version": FULL_AUDIT_VERSION,
                "ocr_version": OCR_VERSION,
                "source_sha256": source_sha,
                "source_filename": pdf_path.name,
                "physical_page_number": page_number,
                "pdf_page_index": page_number - 1,
                "page_number": page_number,
                "language": language,
                "language_evidence": language_evidence,
                "dpi": dpi,
                "coordinate_space": {
                    "ocr_units": "render_pixels",
                    "pdf_units": "points",
                    "pdf_to_ocr_scale_x": scale,
                    "pdf_to_ocr_scale_y": scale,
                },
                "pdf_page": {
                    "width_points": float(page.rect.width),
                    "height_points": float(page.rect.height),
                    "rotation": int(page.rotation),
                },
                "render": {
                    "width": pix.width,
                    "height": pix.height,
                    "sha256": render_sha,
                    "retained_as_preview": page_number in preview_pages,
                },
                "raw_ocr": {
                    "format": "tesseract_tsv",
                    "sha256": _sha256_bytes(raw_tsv),
                    "gzip_file": raw_path.name,
                    "stderr": stderr.strip(),
                },
                "text": text,
                "text_source": "deterministic_reconstruction_from_tsv_words",
                "words": [asdict(word) for word in words],
                "confidence": confidence,
                "parse_warnings": parse_warnings,
                "max_word_length": max_word_length,
                "review_required": bool(review_reasons),
                "review_reasons": review_reasons,
            }
            _write_json(pages_dir / f"page-{page_number:04d}.json", page_payload)

            page_summaries.append(
                {
                    "page_number": page_number,
                    "word_count": len(words),
                    "character_count": len(text),
                    "confidence": confidence,
                    "parse_warning_count": len(parse_warnings),
                    "max_word_length": max_word_length,
                    "review_required": bool(review_reasons),
                    "review_reasons": review_reasons,
                }
            )

            if page_number == 1 or page_number % 25 == 0 or page_number == doc.page_count:
                print(
                    f"OCR full {page_number}/{doc.page_count}: "
                    f"words={len(words)} mean={confidence['mean']} review={bool(review_reasons)}"
                )

    summary = {
        "full_audit_version": FULL_AUDIT_VERSION,
        "ocr_version": OCR_VERSION,
        "source": {
            "path": pdf_path.as_posix(),
            "filename": pdf_path.name,
            "sha256": source_sha,
            "size_bytes": pdf_path.stat().st_size,
            "page_count": doc.page_count,
        },
        "language": language,
        "language_evidence": language_evidence,
        "dpi": dpi,
        "processed_page_count": len(page_summaries),
        "retained_preview_pages": sorted(preview_pages),
        "parse_warning_pages": [p["page_number"] for p in page_summaries if p["parse_warning_count"]],
        "no_word_pages": [p["page_number"] for p in page_summaries if p["word_count"] == 0],
        "suspicious_long_token_pages": [
            p["page_number"] for p in page_summaries if p["max_word_length"] > 120
        ],
        "review_required_pages": [p["page_number"] for p in page_summaries if p["review_required"]],
        "pages": page_summaries,
    }
    _write_json(source_dir / "summary.json", summary)
    doc.close()
    return summary


def _load_structure_pages(structure_root: pathlib.Path) -> list[dict[str, Any]]:
    pages = []
    for page_file in sorted(structure_root.glob("*/pages/page-*.json")):
        pages.append(json.loads(page_file.read_text(encoding="utf-8")))
    return pages


def run_full_document_audit(
    pdf_path: pathlib.Path,
    output_root: pathlib.Path,
    *,
    dpi: int = DEFAULT_DPI,
) -> dict[str, Any]:
    """Run the complete non-publishing extraction audit on one source PDF."""
    pdf_path = pathlib.Path(pdf_path)
    output_root = pathlib.Path(output_root)
    source_sha = _sha256_file(pdf_path)

    ocr_root = output_root / "ocr"
    layout_root = output_root / "layout"
    structure_root = output_root / "structure"

    ocr_summary = _run_full_ocr(pdf_path, ocr_root, dpi=dpi)
    layout_summary = run_layout_prototype(ocr_root, layout_root)
    structure_summary = run_structure_prototype(layout_root, structure_root)
    structure_pages = _load_structure_pages(structure_root)

    class_counts = Counter(page["page_class"] for page in structure_pages)
    procedures = [
        procedure
        for page in structure_pages
        for procedure in page.get("procedures", [])
    ]
    sequence_issues = [
        {
            "page_number": procedure["page_number"],
            "repair_number": procedure["repair_number"],
            "title": procedure.get("title"),
            "step_numbers": procedure.get("step_numbers", []),
            "status": procedure.get("step_sequence_status"),
        }
        for procedure in procedures
        if procedure.get("step_sequence_status") not in {"contiguous", "single_step"}
    ]

    expected_pages = int(ocr_summary["source"]["page_count"])
    layer_counts = {
        "source_pages": expected_pages,
        "ocr_pages": int(ocr_summary["processed_page_count"]),
        "layout_pages": int(layout_summary["sampled_page_count"]),
        "structure_pages": int(structure_summary["sampled_page_count"]),
    }
    layer_count_match = len(set(layer_counts.values())) == 1

    blocking_reasons: list[str] = []
    if not layer_count_match:
        blocking_reasons.append("page_count_mismatch_between_layers")
    if ocr_summary["parse_warning_pages"]:
        blocking_reasons.append("ocr_parse_warnings")
    if ocr_summary["suspicious_long_token_pages"]:
        blocking_reasons.append("suspicious_long_ocr_tokens")
    if ocr_summary["no_word_pages"]:
        blocking_reasons.append("pages_without_ocr_words_require_visual_review")
    if sequence_issues:
        blocking_reasons.append("procedure_step_sequence_issues_require_review")

    # This pipeline is intentionally an extraction/audit stage. Even if the
    # mechanical checks are clean, human/semantic validation has not happened.
    blocking_reasons.append("semantic_and_human_validation_not_completed")

    final_summary = {
        "full_audit_version": FULL_AUDIT_VERSION,
        "source_sha256": source_sha,
        "source_filename": pdf_path.name,
        "layer_counts": layer_counts,
        "layer_count_match": layer_count_match,
        "ocr": {
            "parse_warning_pages": ocr_summary["parse_warning_pages"],
            "no_word_pages": ocr_summary["no_word_pages"],
            "suspicious_long_token_pages": ocr_summary["suspicious_long_token_pages"],
            "review_required_page_count": len(ocr_summary["review_required_pages"]),
        },
        "layout": {
            "possible_two_column_page_count": len(layout_summary["review_required_pages"]),
            "possible_two_column_pages": layout_summary["review_required_pages"],
        },
        "structure": {
            "page_class_counts": dict(sorted(class_counts.items())),
            "procedure_candidate_count": len(procedures),
            "procedure_step_sequence_issue_count": len(sequence_issues),
            "procedure_step_sequence_issues": sequence_issues,
            "repair_numbers": sorted(
                {
                    procedure["repair_number"]
                    for procedure in procedures
                    if procedure.get("repair_number")
                }
            ),
        },
        "publication_gate": "BLOCKED_REVIEW",
        "blocking_reasons": blocking_reasons,
        "statement": (
            "Le full audit reconstruit et audite le document entier, mais ne publie "
            "aucune donnée technique. Les candidats restent soumis au contrôle "
            "structurel, visuel, sémantique et humain."
        ),
    }
    _write_json(output_root / "audit_summary.json", final_summary)
    return final_summary
