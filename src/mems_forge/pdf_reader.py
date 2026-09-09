from __future__ import annotations

import gzip
import hashlib
import json
import math
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

import pymupdf

READER_VERSION = "0.1.0"
BLANK_INK_THRESHOLD = 0.0005
MAX_PREVIEWS = 12


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        return {
            "byte_length": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, pymupdf.Rect):
        return [float(value.x0), float(value.y0), float(value.x1), float(value.y1)]
    if isinstance(value, pymupdf.Point):
        return [float(value.x), float(value.y)]
    if isinstance(value, pymupdf.Matrix):
        return [float(v) for v in value]
    if hasattr(value, "__iter__"):
        try:
            return [_json_safe(v) for v in value]
        except TypeError:
            pass
    return str(value)


def _bbox(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, pymupdf.Rect):
        return [float(value.x0), float(value.y0), float(value.x1), float(value.y1)]
    if isinstance(value, (list, tuple)) and len(value) == 4:
        result = [_float(v) for v in value]
        if all(v is not None for v in result):
            return [float(v) for v in result]
    return None


def _write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class JsonlGzWriter:
    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._stream = gzip.open(path, "wt", encoding="utf-8", newline="\n")

    def write(self, payload: dict[str, Any]) -> None:
        self._stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "JsonlGzWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _count_annotations(page: pymupdf.Page) -> int:
    try:
        annots = page.annots()
        if annots is None:
            return 0
        return sum(1 for _ in annots)
    except Exception:
        return 0


def _visual_fingerprint(page: pymupdf.Page) -> dict[str, Any]:
    # Low-resolution grayscale replay is intentionally independent from native text extraction.
    # It is used to detect visible content that a native extractor may have missed.
    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.35, 0.35), colorspace=pymupdf.csGRAY, alpha=False)
    samples = pix.samples
    ink = sum(1 for value in samples if value < 250)
    total = max(1, len(samples))
    return {
        "width": pix.width,
        "height": pix.height,
        "sha256": hashlib.sha256(samples).hexdigest(),
        "ink_fraction": ink / total,
    }


def _line_layout_signal(lines: list[dict[str, Any]], page_width: float) -> dict[str, Any]:
    usable = []
    full_width = 0
    for line in lines:
        text = line.get("text", "").strip()
        box = line.get("bbox")
        if len(text) < 4 or not box or page_width <= 0:
            continue
        width_ratio = max(0.0, (box[2] - box[0]) / page_width)
        if width_ratio >= 0.72:
            full_width += 1
            continue
        center = (box[0] + box[2]) / 2.0
        usable.append((center, box[1], box[3], width_ratio))

    left = [item for item in usable if item[0] < page_width * 0.46]
    right = [item for item in usable if item[0] > page_width * 0.54]
    possible = len(left) >= 3 and len(right) >= 3

    vertical_overlap_ratio = 0.0
    if possible:
        ly0, ly1 = min(v[1] for v in left), max(v[2] for v in left)
        ry0, ry1 = min(v[1] for v in right), max(v[2] for v in right)
        overlap = max(0.0, min(ly1, ry1) - max(ly0, ry0))
        denominator = max(1.0, min(ly1 - ly0, ry1 - ry0))
        vertical_overlap_ratio = overlap / denominator

    # This is a signal only. It must never be treated as proof of a two-column page.
    return {
        "possible_multicolumn": possible,
        "left_candidate_lines": len(left),
        "right_candidate_lines": len(right),
        "full_width_lines": full_width,
        "vertical_overlap_ratio": vertical_overlap_ratio,
        "status": "review_required" if possible else "native_geometry_preserved",
    }


def _page_kind(
    text_chars: int,
    image_count: int,
    vector_count: int,
    links_count: int,
    annotation_count: int,
    ink_fraction: float,
) -> str:
    if text_chars > 0:
        return "native_text_with_visuals" if (image_count or vector_count) else "native_text"
    if image_count and vector_count:
        return "raster_and_vector_without_native_text"
    if image_count:
        return "raster_without_native_text"
    if vector_count:
        return "vector_without_native_text"
    if links_count or annotation_count:
        return "interactive_without_native_text"
    if ink_fraction <= BLANK_INK_THRESHOLD:
        return "blank_verified_by_render"
    return "visible_content_not_explained_by_native_objects"


def _review_id(source_sha: str, page_number: int, code: str) -> str:
    payload = f"{source_sha}:{page_number}:{code}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _review_items_for_page(
    source_sha: str,
    page_number: int,
    page_kind: str,
    layout: dict[str, Any],
    ink_fraction: float,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def add(code: str, severity: str, reason: str) -> None:
        items.append(
            {
                "review_id": _review_id(source_sha, page_number, code),
                "source_sha256": source_sha,
                "page_number": page_number,
                "code": code,
                "severity": severity,
                "status": "unresolved",
                "reason": reason,
            }
        )

    if layout.get("possible_multicolumn"):
        add(
            "READING_ORDER_REVIEW",
            "blocking",
            "La géométrie présente un signal de plusieurs colonnes. Aucun ordre de lecture n'est imposé automatiquement.",
        )

    if page_kind in {"raster_without_native_text", "raster_and_vector_without_native_text"}:
        add(
            "OCR_REQUIRED",
            "blocking",
            "La page contient du contenu raster visible sans texte natif suffisant. Une passe OCR contrôlée est requise.",
        )

    if page_kind in {"vector_without_native_text", "raster_and_vector_without_native_text"}:
        add(
            "VECTOR_PASS_REQUIRED",
            "blocking",
            "La page contient du contenu vectoriel sans texte natif. Le pipeline vectoriel séparé doit l'examiner.",
        )

    if page_kind == "visible_content_not_explained_by_native_objects":
        add(
            "VISIBLE_CONTENT_UNRESOLVED",
            "blocking",
            f"Le rendu montre du contenu visible (ink_fraction={ink_fraction:.6f}) non expliqué par les objets natifs détectés.",
        )

    return items


def _iter_text_records(
    text_dict: dict[str, Any],
    source_sha: str,
    page_number: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    blocks: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    lines_for_layout: list[dict[str, Any]] = []

    for block_index, block in enumerate(text_dict.get("blocks", [])):
        block_type = int(block.get("type", -1))
        block_record = {
            "source_sha256": source_sha,
            "page_number": page_number,
            "block_index": block_index,
            "block_type": block_type,
            "bbox": _bbox(block.get("bbox")),
            "number": block.get("number"),
        }

        if block_type != 0:
            # Image occurrences are recorded separately with xref/hash metadata.
            block_record["native_payload"] = {
                k: _json_safe(v)
                for k, v in block.items()
                if k not in {"image", "lines"}
            }
            blocks.append(block_record)
            continue

        block_lines = block.get("lines", [])
        text_parts: list[str] = []
        for line_index, line in enumerate(block_lines):
            line_text_parts: list[str] = []
            for span_index, span in enumerate(line.get("spans", [])):
                text = str(span.get("text", ""))
                line_text_parts.append(text)
                spans.append(
                    {
                        "source_sha256": source_sha,
                        "page_number": page_number,
                        "block_index": block_index,
                        "line_index": line_index,
                        "span_index": span_index,
                        "text": text,
                        "bbox": _bbox(span.get("bbox")),
                        "origin": _json_safe(span.get("origin")),
                        "font": span.get("font"),
                        "size": _float(span.get("size")),
                        "flags": span.get("flags"),
                        "char_flags": span.get("char_flags"),
                        "bidi": span.get("bidi"),
                        "color": span.get("color"),
                        "alpha": span.get("alpha"),
                        "ascender": _float(span.get("ascender")),
                        "descender": _float(span.get("descender")),
                    }
                )

            line_text = "".join(line_text_parts)
            text_parts.append(line_text)
            lines_for_layout.append(
                {
                    "block_index": block_index,
                    "line_index": line_index,
                    "text": line_text,
                    "bbox": _bbox(line.get("bbox")),
                    "direction": _json_safe(line.get("dir")),
                    "writing_mode": line.get("wmode"),
                }
            )

        block_record["text"] = "\n".join(text_parts)
        block_record["line_count"] = len(block_lines)
        blocks.append(block_record)

    return blocks, spans, lines_for_layout


def _image_records(page: pymupdf.Page, source_sha: str, page_number: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        images = page.get_image_info(hashes=True, xrefs=True)
    except Exception:
        images = []
    for index, info in enumerate(images):
        digest = info.get("digest")
        records.append(
            {
                "source_sha256": source_sha,
                "page_number": page_number,
                "image_index": index,
                "xref": info.get("xref"),
                "bbox": _bbox(info.get("bbox")),
                "width": info.get("width"),
                "height": info.get("height"),
                "colorspace": info.get("colorspace"),
                "bits_per_component": info.get("bpc"),
                "xres": info.get("xres"),
                "yres": info.get("yres"),
                "digest": digest.hex() if isinstance(digest, bytes) else _json_safe(digest),
                "transform": _json_safe(info.get("transform")),
                "native": _json_safe({k: v for k, v in info.items() if k != "digest"}),
            }
        )
    return records


def _vector_records(page: pymupdf.Page, source_sha: str, page_number: int) -> list[dict[str, Any]]:
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    records = []
    for index, drawing in enumerate(drawings):
        records.append(
            {
                "source_sha256": source_sha,
                "page_number": page_number,
                "drawing_index": index,
                "rect": _bbox(drawing.get("rect")),
                "geometry": _json_safe(drawing),
            }
        )
    return records


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value or "document.pdf"


def analyze_pdf(pdf_path: pathlib.Path, output_root: pathlib.Path) -> dict[str, Any]:
    pdf_path = pathlib.Path(pdf_path)
    source_sha = sha256_file(pdf_path)
    output_dir = pathlib.Path(output_root) / source_sha
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(pdf_path)
    if doc.needs_pass:
        raise RuntimeError(f"PDF protégé par mot de passe: {pdf_path}")

    source = {
        "reader_version": READER_VERSION,
        "pymupdf_version": getattr(pymupdf, "__version__", "unknown"),
        "source_path": pdf_path.as_posix(),
        "filename": pdf_path.name,
        "size_bytes": pdf_path.stat().st_size,
        "sha256": source_sha,
        "page_count": doc.page_count,
        "is_repaired": bool(getattr(doc, "is_repaired", False)),
        "metadata": _json_safe(doc.metadata),
    }
    _write_json(output_dir / "source.json", source)
    try:
        _write_json(output_dir / "toc.json", _json_safe(doc.get_toc(simple=False)))
    except Exception:
        _write_json(output_dir / "toc.json", [])

    counts = {
        "pages": 0,
        "native_text_pages": 0,
        "raster_without_native_text_pages": 0,
        "vector_without_native_text_pages": 0,
        "blank_verified_pages": 0,
        "unexplained_visible_pages": 0,
        "possible_multicolumn_pages": 0,
        "raw_blocks": 0,
        "raw_spans": 0,
        "image_occurrences": 0,
        "vector_drawings": 0,
        "blocking_review_items": 0,
    }

    preview_pages: set[int] = {0, 1, 2}

    with (
        JsonlGzWriter(output_dir / "pages.jsonl.gz") as pages_out,
        JsonlGzWriter(output_dir / "raw_blocks.jsonl.gz") as blocks_out,
        JsonlGzWriter(output_dir / "raw_spans.jsonl.gz") as spans_out,
        JsonlGzWriter(output_dir / "images.jsonl.gz") as images_out,
        JsonlGzWriter(output_dir / "vectors.jsonl.gz") as vectors_out,
        JsonlGzWriter(output_dir / "review_items.jsonl.gz") as review_out,
    ):
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            page_number = page_index + 1
            text_dict = page.get_text("dict", sort=False)
            blocks, spans, lines = _iter_text_records(text_dict, source_sha, page_number)
            images = _image_records(page, source_sha, page_number)
            vectors = _vector_records(page, source_sha, page_number)
            visual = _visual_fingerprint(page)
            page_rect = page.rect
            layout = _line_layout_signal(lines, float(page_rect.width))
            text_chars = sum(len(span.get("text", "")) for span in spans)
            links_count = len(page.get_links())
            annotation_count = _count_annotations(page)
            kind = _page_kind(
                text_chars,
                len(images),
                len(vectors),
                links_count,
                annotation_count,
                visual["ink_fraction"],
            )
            review_items = _review_items_for_page(
                source_sha,
                page_number,
                kind,
                layout,
                visual["ink_fraction"],
            )

            page_record = {
                "source_sha256": source_sha,
                "page_number": page_number,
                "page_label": page.get_label(),
                "width": float(page_rect.width),
                "height": float(page_rect.height),
                "rotation": int(page.rotation),
                "text_chars": text_chars,
                "block_count": len(blocks),
                "span_count": len(spans),
                "image_occurrence_count": len(images),
                "vector_drawing_count": len(vectors),
                "links_count": links_count,
                "annotation_count": annotation_count,
                "page_kind": kind,
                "layout_signal": layout,
                "visual_replay": visual,
                "review_item_count": len(review_items),
            }
            pages_out.write(page_record)
            for record in blocks:
                blocks_out.write(record)
            for record in spans:
                spans_out.write(record)
            for record in images:
                images_out.write(record)
            for record in vectors:
                vectors_out.write(record)
            for record in review_items:
                review_out.write(record)

            counts["pages"] += 1
            counts["raw_blocks"] += len(blocks)
            counts["raw_spans"] += len(spans)
            counts["image_occurrences"] += len(images)
            counts["vector_drawings"] += len(vectors)
            counts["blocking_review_items"] += sum(1 for r in review_items if r["severity"] == "blocking")

            if text_chars > 0:
                counts["native_text_pages"] += 1
            if kind in {"raster_without_native_text", "raster_and_vector_without_native_text"}:
                counts["raster_without_native_text_pages"] += 1
            if kind in {"vector_without_native_text", "raster_and_vector_without_native_text"}:
                counts["vector_without_native_text_pages"] += 1
            if kind == "blank_verified_by_render":
                counts["blank_verified_pages"] += 1
            if kind == "visible_content_not_explained_by_native_objects":
                counts["unexplained_visible_pages"] += 1
            if layout["possible_multicolumn"]:
                counts["possible_multicolumn_pages"] += 1

            if review_items and len(preview_pages) < MAX_PREVIEWS:
                preview_pages.add(page_index)

    previews_dir = output_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    preview_written = []
    for page_index in sorted(p for p in preview_pages if 0 <= p < doc.page_count)[:MAX_PREVIEWS]:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(0.9, 0.9), colorspace=pymupdf.csGRAY, alpha=False)
        preview_path = previews_dir / f"page-{page_index + 1:04d}.png"
        pix.save(preview_path)
        preview_written.append(preview_path.name)

    doc.close()

    summary = {
        "reader_version": READER_VERSION,
        "source": source,
        "counts": counts,
        "publication_gate": {
            "status": "BLOCKED_REVIEW" if counts["blocking_review_items"] else "RAW_READER_PASS",
            "reason": (
                f"{counts['blocking_review_items']} élément(s) bloquant(s) à valider"
                if counts["blocking_review_items"]
                else "Aucun élément bloquant détecté par cette première passe brute"
            ),
            "note": "RAW_READER_PASS ne signifie pas que le contenu est sémantiquement validé ni publiable.",
        },
        "preview_files": preview_written,
    }
    _write_json(output_dir / "summary.json", summary)
    return summary
