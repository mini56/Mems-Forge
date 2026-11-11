"""Build a verifiable, source-preserving document pack from MEMS Forge's reader.

A raw pack is NOT a validated technical-knowledge publication. It includes
the unmodified PDF and a searchable provenance-backed SQLite index. OCR and
uncertain interpretation never become approved facts by mere extraction.
"""
from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import re
import tempfile
import zipfile

from . import __version__
from .database import initialize_database
from .pdf_reader import analyze_pdf, sha256_file
from .ocr_reader import _parse_words, _words_to_text
from .ocr_strategy_audit import _phase_bounded_numbered_steps
from .structure_candidates import STEP_RE, _normalise_space
from .procedure_fragments import _measured_value_candidates


def records(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _bbox(value):
    if not isinstance(value, list) or len(value) != 4:
        return (None, None, None, None)
    return tuple(value)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _adaptive_ocr_evidence(page_data: dict, page_number: int) -> tuple[list, list]:
    """Separate legible source-backed OCR variants from unresolved image regions.

    A missing OCR reading is not a technical fact, and it must not abort a
    candidate's entire source inventory if the original page explicitly
    records the failure. It becomes a page/region-scoped issue in SQLite.
    Any unaccounted-for empty reading or broken geometry is a hard error.
    """
    extraction = page_data.get("adaptive_extraction") or {}
    observations = []
    recovered = extraction.get("recovered_list")
    if recovered:
        observations.append((
            "recovered_numbered_list", "psm6_200dpi_400dpi",
            recovered.get("roi_pdf"), recovered.get("candidate_text"),
            bool(recovered.get("independent_render_readings_agree")),
        ))
    for side, reading in sorted((extraction.get("columns") or {}).items()):
        if side not in ("left", "right") or not isinstance(reading, dict):
            raise ValueError(f"Unexpected adaptive OCR region on page {page_number}")
        observations.append((
            "column_" + side,
            str(reading.get("candidate_mode") or "unknown"),
            reading.get("bbox_pdf"), reading.get("text"), False,
        ))
    declared = set(page_data.get("adaptive_empty_regions") or [])
    recorded_reasons = page_data.get("review_reasons") or []
    variants = []
    issues = []
    for region, method, bbox, candidate_text, crosschecked in observations:
        if (not isinstance(bbox, list) or len(bbox) != 4 or
                not all(type(value) in (float, int) for value in bbox) or
                bbox[0] >= bbox[2] or bbox[1] >= bbox[3] or
                not isinstance(candidate_text, str)):
            raise ValueError(
                f"Invalid adaptive OCR provenance on page {page_number}: {region}"
            )
        if candidate_text.strip():
            if region in declared:
                raise ValueError(
                    f"Contradictory adaptive OCR review on page {page_number}: {region}"
                )
            variants.append((region, method, bbox, candidate_text, crosschecked))
            continue
        # Fail closed for missing text, without confusing "nothing recognized"
        # with evidence that a scanned figure contains no information.
        if (region not in declared or not page_data.get("review_required") or
                "adaptive_region_without_text" not in recorded_reasons):
            raise ValueError(
                f"Unrecorded empty adaptive OCR region on page {page_number}: {region}"
            )
        issues.append((region, method, bbox, "ADAPTIVE_REGION_NO_TEXT"))
    if declared != {item[0] for item in issues}:
        raise ValueError(
            f"Unmatched adaptive OCR review regions on page {page_number}"
        )
    return variants, issues


_NUMBERED_SOURCE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+\w")

# PDF positions from the 400-dpi OCR image are rounded to full image pixels.
# Exact PDF crop edges need not map to full pixels. Preserve the *measured*
# coordinates; allow up to one pixel of quantization, never a broad tolerance.
_SOURCE_PIXEL_TOLERANCE_POINTS = 72.0 / 400.0


def _bbox_within_original_ocr_region(
    bbox: list, roi: list, width: float, height: float,
) -> bool:
    if (len(bbox) != 4 or len(roi) != 4
            or any(type(v) not in (int, float) for v in (*bbox, *roi))):
        return False
    t = _SOURCE_PIXEL_TOLERANCE_POINTS
    return (
        0 <= bbox[0] < bbox[2] <= width + t
        and 0 <= bbox[1] < bbox[3] <= height + t
        and roi[0] - t <= bbox[0] < bbox[2] <= roi[2] + t
        and roi[1] - t <= bbox[1] < bbox[3] <= roi[3] + t
    )


def _anchored_source_items(page_data: dict, page_number: int) -> list[dict]:
    """Read only crosschecked, original-PDF OCR coordinates, never AI boxes.

    A complete source text list and its word geometries must agree exactly.
    No technical interpretation or model applicability is authorized here.
    """
    recovered = (page_data.get("adaptive_extraction") or {}).get("recovered_list")
    if not recovered:
        return []
    items = recovered.get("anchored_items") or []
    if not recovered.get("ocr_locations_crosschecked"):
        if items:
            raise ValueError(f"Unverified OCR source anchors on page {page_number}")
        return []
    if not recovered.get("independent_render_readings_agree"):
        raise ValueError(f"OCR source anchors contradict readings on page {page_number}")
    text_lines = [
        line.strip() for line in str(recovered.get("candidate_text") or "").splitlines()
        if _NUMBERED_SOURCE.match(line)
    ]
    numbers = recovered.get("numbering", {}).get("numbers")
    if (not items or len(items) != len(text_lines) or
            numbers != list(range(1, len(items) + 1)) or
            int(recovered.get("anchored_item_count", -1)) != len(items)):
        raise ValueError(f"Incomplete OCR original source anchors on page {page_number}")
    page_size = page_data.get("pdf_page") or {}
    width, height = page_size.get("width_points"), page_size.get("height_points")
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        raise ValueError(f"Missing PDF dimensions for OCR anchors on page {page_number}")
    roi = recovered.get("roi_pdf")
    if not isinstance(roi, list) or len(roi) != 4:
        raise ValueError(f"Missing OCR original ROI on page {page_number}")
    for index, (item, expected_text) in enumerate(zip(items, text_lines), 1):
        bbox = item.get("bbox_pdf")
        if (item.get("number") != index or item.get("source_text") != expected_text
                or item.get("approved_for_ai") is not False
                or item.get("validation_state") != "OCR_LOCATION_CANDIDATE"
                or item.get("source") != "original_pdf_tesseract_400dpi_tsv"
                or not isinstance(bbox, list)
                or not _bbox_within_original_ocr_region(
                    bbox, roi, float(width), float(height)
                )):
            raise ValueError(f"Invalid OCR original source item {index} on page {page_number}")
    return items


def _numbered_column_evidence(page_data: dict, page_number: int) -> list[tuple]:
    """Check each numbered OCR line against its parent *column OCR text*.

    A readable numbered line is not necessarily a procedural instruction: it
    may be an illustration legend or an unlabeled continuation. The column ROI
    is the only positional evidence available; do not invent line-level boxes.
    """
    output = []
    columns = ((page_data.get("adaptive_extraction") or {})
               .get("columns") or {})
    page_size = page_data.get("pdf_page") or {}
    width, height = page_size.get("width_points"), page_size.get("height_points")
    for side, reading in sorted(columns.items()):
        if side not in ("left", "right") or not isinstance(reading, dict):
            raise ValueError(f"Invalid numbered column on page {page_number}")
        recorded = reading.get("phase_segments")
        if recorded is None:  # Older raw OCR records contain no phase grouping.
            continue
        if not isinstance(recorded, list):
            raise ValueError(f"Invalid OCR phase segments on page {page_number}")
        exact = _phase_bounded_numbered_steps(str(reading.get("text") or ""))
        if recorded != exact:
            raise ValueError(f"OCR phase lines not derived from source on page {page_number}")
        if not exact:
            continue
        bbox = reading.get("bbox_pdf")
        if (not isinstance(bbox, list) or
                not isinstance(width, (int, float)) or
                not isinstance(height, (int, float)) or
                not _bbox_within_original_ocr_region(
                    bbox, [0, 0, float(width), float(height)],
                    float(width), float(height),
                )):
            raise ValueError(f"Numbered column source geometry invalid on page {page_number}")
        for index, group in enumerate(exact):
            if (not group.get("steps") or
                    group.get("approved_for_technical_database") is not False or
                    group.get("source_kind") not in (
                        "numbered_lines_under_explicit_phase",
                        "numbered_lines_unclassified",
                    ) or
                    (not group.get("source_heading_explicit") and
                     group.get("phase") != "unassigned") or
                    (not group.get("source_heading_explicit") and
                     not group.get("review_required"))):
                raise ValueError(f"Unsafe OCR numbered phase on page {page_number}")
            for item in group["steps"]:
                if (item.get("approved_for_ai") is not False or
                        item.get("validation_state") != "OCR_NUMBERED_LINE_CANDIDATE"
                        or not item.get("source_text") or
                        int(item.get("number", 0)) < 1):
                    raise ValueError(f"Unsafe numbered OCR line on page {page_number}")
            output.append((side, index, bbox, group))
    return output


def _immutable_ocr_line_index(words: list) -> dict[tuple[int, int, int], dict]:
    """Derive line text and rectangles exclusively from packaged Tesseract TSV."""
    grouped: dict[tuple[int, int, int], list] = {}
    for word in words:
        key = (word.block_num, word.par_num, word.line_num)
        grouped.setdefault(key, []).append(word)
    index = {}
    for key, members in grouped.items():
        members.sort(key=lambda w: w.left)
        index[key] = {
            "text": " ".join(w.text for w in members),
            "bbox": [
                min(w.left for w in members),
                min(w.top for w in members),
                max(w.left + w.width for w in members),
                max(w.top + w.height for w in members),
            ],
        }
    return index


def _review_only_cross_page_links(
    fragment_root: Path | None, source_sha: str, page_count: int,
) -> list[dict]:
    """Import only physically adjacent, source-identified review candidates.

    This is intentionally NOT a procedural reconstruction. The result may be
    inspected in SQLite but can never become an approved technical instruction.
    """
    if fragment_root is None:
        return []
    path = Path(fragment_root) / "summary.json"
    if not path.is_file():
        raise ValueError("CONTINUATION_EVIDENCE_REPORT_MISSING")
    report = json.loads(path.read_text(encoding="utf-8"))
    if (report.get("source_pdf_sha256") != source_sha or
            report.get("automatic_procedure_merges") != 0 or
            report.get("publication_gate") not in (
                "BLOCKED_REVIEW", "NO_FRAGMENT_BLOCKER"
            )):
        raise ValueError("CONTINUATION_EVIDENCE_PROVENANCE_MISMATCH")
    page_map = report.get("original_physical_page_map") or {}
    if (set(page_map) != {str(n) for n in range(1, page_count + 1)} or
            any(type(v) is not int or v < 1 for v in page_map.values()) or
            [page_map[str(n)] for n in range(1, page_count + 1)]
            != sorted(set(page_map.values()))):
        raise ValueError("CONTINUATION_PHYSICAL_PAGE_MAP_INVALID")
    links = report.get("cross_page_link_candidates") or []
    if (not isinstance(links, list) or
            len(links) != report.get("cross_page_link_candidate_count")):
        raise ValueError("CONTINUATION_CANDIDATE_COUNT_MISMATCH")
    result = []
    seen = set()
    for link in links:
        start, end = link.get("from_page"), link.get("to_page")
        evidence = link.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError("CONTINUATION_EVIDENCE_INVALID")
        has_chapter = (
            "same_manufacturer_chapter_candidate" in evidence
            and link.get("chapter_id") != "UNRESOLVED"
        )
        has_unresolved_chapter_fallback = (
            link.get("chapter_id") == "UNRESOLVED"
            and "chapter_unresolved_review_required" in evidence
            and "original_right_column_to_next_left_column" in evidence
            and "new_right_column_has_distinct_repair_same_numbered_family" in evidence
            and "explicit_refit_phase_restart" in evidence
            and link.get("candidate_phase") == "refit"
            and link.get("to_region") == "left"
            and link.get("origin_procedure_page") == start
        )
        if not (has_chapter or has_unresolved_chapter_fallback):
            raise ValueError("CONTINUATION_CHAPTER_EVIDENCE_UNSUPPORTED")
        if (type(start) is not int or type(end) is not int
                or not 1 <= start < end <= page_count or end != start + 1
                or page_map[str(start)] + 1 != page_map[str(end)]
                or link.get("source_pdf_sha256") != source_sha
                or link.get("from_original_physical_page") != page_map[str(start)]
                or link.get("to_original_physical_page") != page_map[str(end)]
                or link.get("status") != "REVIEW_REQUIRED"
                or link.get("approved_for_ai") is not False
                or not isinstance(link.get("chapter_id"), str)
                or not link["chapter_id"]
                or not isinstance(link.get("candidate_repair_number"), str)
                or not re.fullmatch(
                    r"[0-9]+(?:\.[0-9]+){1,4}",
                    link["candidate_repair_number"]
                )
                or link.get("to_region") not in ("left", "right", "body")
                or link.get("origin_procedure_page", 0) > start
                or "adjacent_original_physical_pages" not in link.get("evidence", [])
                or "adjacent_original_physical_pages" not in evidence):
            raise ValueError("CONTINUATION_CANDIDATE_UNSAFE_SOURCE")
        numbers = link.get("fragment_step_numbers")
        source_lines = link.get("fragment_source_lines")
        if (not isinstance(numbers, list) or not numbers
                or any(type(v) is not int for v in numbers)
                or numbers != list(range(numbers[0], numbers[0] + len(numbers)))
                or not isinstance(source_lines, list)
                or len(source_lines) != len(numbers)
                or any(not isinstance(item, dict) or
                       not isinstance(item.get("source_line_key"), list) or
                       not isinstance(item.get("bbox"), list) or
                       len(item.get("bbox")) != 4 or
                       not str(item.get("text", "")).strip()
                       for item in source_lines)):
            raise ValueError("CONTINUATION_CANDIDATE_UNTRACED_STEPS")
        # Every link must match the extracted fragment of that *same* page.
        detail = Path(fragment_root) / source_sha / "pages" / f"page-{end:04d}.json"
        if not detail.is_file():
            raise ValueError("CONTINUATION_PAGE_EVIDENCE_MISSING")
        page = json.loads(detail.read_text(encoding="utf-8"))
        matches = [
            fragment for fragment in page.get("fragments", [])
            if fragment.get("region") == link["to_region"]
            and fragment.get("step_numbers") == numbers
            and [i.get("source") for i in fragment.get("numbered_lines", [])]
            == source_lines
        ]
        if len(matches) != 1:
            raise ValueError("CONTINUATION_NOT_GROUNDED_IN_OCR_FRAGMENT")
        full_steps = link.get("fragment_complete_steps")
        if (not isinstance(full_steps, list) or len(full_steps) != len(numbers)):
            raise ValueError("CONTINUATION_COMPLETE_STEPS_MISSING")
        for index, (item, original_step) in enumerate(zip(
            full_steps, matches[0]["numbered_lines"]
        )):
            origins = original_step.get("source_lines") or [original_step["source"]]
            if (type(item.get("number")) is not int
                    or item["number"] != numbers[index]
                    or item["number"] != original_step["number"]
                    or item.get("text") != original_step.get("text")
                    or item.get("source_lines") != origins
                    or item.get("source_line_count") != len(origins)
                    or item.get("validation_state")
                    != "OCR_MULTILINE_CANDIDATE_UNVERIFIED"
                    or item.get("approved_for_ai") is not False
                    or item.get("measurement_mentions") !=
                    _measured_value_candidates(item["text"])):
                raise ValueError("CONTINUATION_STEP_SOURCE_TEXT_CONFLICT")
            for pos, source_line in enumerate(origins):
                match = STEP_RE.match(str(source_line.get("text", ""))) if pos == 0 else None
                if (not isinstance(source_line.get("source_line_key"), list)
                        or len(source_line["source_line_key"]) != 3
                        or not isinstance(source_line.get("bbox"), list)
                        or len(source_line["bbox"]) != 4
                        or source_line.get("region") != link["to_region"]
                        or (pos == 0 and
                            (match is None or int(match.group(1)) != item["number"]))):
                    raise ValueError("CONTINUATION_STEP_OCR_LINE_UNTRACEABLE")
            reconstructed = _normalise_space(" ".join(
                [STEP_RE.match(origins[0]["text"]).group(2)] +
                [source_line["text"] for source_line in origins[1:]]
            ))
            if reconstructed != item["text"]:
                raise ValueError("CONTINUATION_STEP_MULTILINE_TEXT_NOT_FROM_SOURCE")
        key = (start, end, link["to_region"], tuple(numbers))
        if key in seen:
            raise ValueError("DUPLICATE_PROCEDURE_CONTINUATION")
        seen.add(key)
        result.append(link)
    return result


def _review_only_source_notices(
    fragment_root: Path | None, source_sha: str, page_count: int,
) -> list[dict]:
    """Import source WARNING/CAUTION/NOTE OCR without assigning a repair."""
    if fragment_root is None:
        return []
    root = Path(fragment_root)
    report = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    if report.get("source_pdf_sha256") != source_sha:
        raise ValueError("SAFETY_NOTICE_SOURCE_DOCUMENT_MISMATCH")
    original_map = report.get("original_physical_page_map") or {}
    if set(original_map) != {str(n) for n in range(1, page_count + 1)}:
        raise ValueError("SAFETY_NOTICE_PAGE_MAP_INCOMPLETE")
    result: list[dict] = []
    for page_number in range(1, page_count + 1):
        page_file = root / source_sha / "pages" / f"page-{page_number:04d}.json"
        if not page_file.is_file():
            raise ValueError("SAFETY_NOTICE_PAGE_EVIDENCE_MISSING")
        page = json.loads(page_file.read_text(encoding="utf-8"))
        notices = page.get("source_notices") or []
        if (page.get("page_number") != page_number
                or page.get("source_notice_count") != len(notices)):
            raise ValueError("SAFETY_NOTICE_PAGE_EVIDENCE_INCONSISTENT")
        for notice in notices:
            lines = notice.get("source_lines")
            if (notice.get("page_number") != page_number
                    or notice.get("kind") not in ("WARNING", "CAUTION", "NOTE")
                    or notice.get("region") not in ("left", "right", "body")
                    or notice.get("relationship_to_procedure") != "UNRESOLVED"
                    or notice.get("validation_state")
                    != "OCR_SOURCE_NOTICE_UNVERIFIED"
                    or notice.get("approved_for_ai") is not False
                    or not isinstance(lines, list) or not lines
                    or notice.get("source_line_count") != len(lines)
                    or not re.match(r"^(?:WARNING|CAUTION|NOTE)\s*:",
                                    str(lines[0].get("text", "")), re.IGNORECASE)
                    or notice.get("source_text") != " ".join(
                        str(item.get("text", "")) for item in lines
                    )):
                raise ValueError("SAFETY_NOTICE_INVALID_OR_INVENTED")
            if not all(
                isinstance(line.get("source_line_key"), list)
                and len(line["source_line_key"]) == 3
                and isinstance(line.get("bbox"), list)
                and len(line["bbox"]) == 4
                and line.get("region") == notice["region"]
                for line in lines
            ):
                raise ValueError("SAFETY_NOTICE_UNTRACEABLE_SOURCE")
            result.append({
                **notice,
                "original_physical_page": original_map[str(page_number)],
            })
    if len(result) != report.get("source_notice_count"):
        raise ValueError("SAFETY_NOTICE_COUNT_MISMATCH")
    return result


def build_document_pack(
    pdf_path: Path, output_path: Path, *, reader_root: Path | None = None,
    ocr_root: Path | None = None,
    fragment_root: Path | None = None,
) -> dict:
    """Produce a candidate pack only, never modify the published database."""
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise ValueError("An existing PDF file is required")
    source_sha = sha256_file(pdf_path)
    with tempfile.TemporaryDirectory(prefix="mems-forge-pack-") as temporary:
        tmp = Path(temporary)
        analysis_root = reader_root if reader_root is not None else tmp / "reader"
        summary_file = Path(analysis_root) / source_sha / "summary.json"
        if summary_file.is_file():
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
        else:
            summary = analyze_pdf(pdf_path, Path(analysis_root))
        if (summary["source"]["sha256"] != source_sha or
                summary["source"]["size_bytes"] != pdf_path.stat().st_size):
            raise ValueError("Reader output does not belong to this PDF")
        data_root = Path(analysis_root) / source_sha
        expected_pages = int(summary["source"]["page_count"])
        if expected_pages < 1:
            raise ValueError("Empty or invalid source document")
        database_file = tmp / "knowledge.sqlite"
        initialize_database(database_file, __version__)
        revision = "REV_" + source_sha
        document = "DOC_" + source_sha[:24]
        count = {"pages": 0, "blocks": 0, "spans": 0, "image_occurrences": 0,
                 "reviews": 0, "ocr_pages": 0, "ocr_words": 0, "ocr_variants": 0,
                 "ocr_issues": 0, "ocr_source_items": 0,
                 "ocr_numbered_segments": 0, "ocr_numbered_lines": 0,
                 "ocr_page_content": 0, "procedure_continuation_candidates": 0,
                 "procedure_continuation_steps": 0,
                 "safety_notice_candidates": 0}
        ocr_members = {}
        with closing(sqlite3.connect(database_file)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "INSERT INTO documents(document_id,title,document_type) VALUES(?,?,?)",
                (document, pdf_path.name, "PDF"),
            )
            db.execute(
                "INSERT INTO document_revisions(revision_id,document_id,"
                "source_filename,sha256,source_size,page_count,forge_version)"
                " VALUES(?,?,?,?,?,?,?)",
                (revision, document, pdf_path.name, source_sha,
                 pdf_path.stat().st_size, expected_pages, __version__),
            )
            for page in records(data_root / "pages.jsonl.gz"):
                n = int(page["page_number"])
                if n != count["pages"] + 1:
                    raise ValueError(f"Non-contiguous PDF page {n}")
                db.execute(
                    "INSERT INTO source_pages(page_id,revision_id,physical_page_number,"
                    "printed_page_label,width,height,rotation,content_kind)"
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (f"{revision}:p{n}", revision, n, page.get("page_label"),
                     page.get("width"), page.get("height"), page.get("rotation"),
                     page.get("page_kind")),
                )
                count["pages"] += 1
            if count["pages"] != expected_pages:
                raise ValueError("Missing pages in reader evidence")
            for block in records(data_root / "raw_blocks.jsonl.gz"):
                n = int(block["page_number"])
                ix = int(block["block_index"])
                b = _bbox(block.get("bbox"))
                db.execute(
                    "INSERT INTO raw_blocks(raw_block_id,page_id,extraction_method,"
                    "block_kind,source_text,bbox_x0,bbox_y0,bbox_x1,bbox_y1,"
                    "geometric_order,engine_version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (f"{revision}:p{n}:b{ix}", f"{revision}:p{n}",
                     "pymupdf_native", str(block.get("block_type", "unknown")),
                     block.get("text", ""), *b, ix, __version__),
                )
                count["blocks"] += 1
            for span in records(data_root / "raw_spans.jsonl.gz"):
                n = int(span["page_number"])
                i = int(span["block_index"])
                line = int(span["line_index"])
                ix = int(span["span_index"])
                b = _bbox(span.get("bbox"))
                db.execute(
                    "INSERT INTO raw_spans(raw_span_id,raw_block_id,source_text,"
                    "bbox_x0,bbox_y0,bbox_x1,bbox_y1,font_name,font_size,"
                    "span_order) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (f"{revision}:p{n}:b{i}:l{line}:s{ix}", f"{revision}:p{n}:b{i}",
                     span.get("text", ""), *b, span.get("font"),
                     span.get("size"), ix),
                )
                count["spans"] += 1
            db.execute(
                "CREATE TABLE pack_ocr_pages("
                "page_id TEXT PRIMARY KEY REFERENCES source_pages(page_id),"
                "text TEXT NOT NULL,language TEXT,confidence_mean REAL,"
                "review_required INTEGER NOT NULL,raw_tsv_sha256 TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE pack_ocr_words("
                "page_id TEXT NOT NULL REFERENCES pack_ocr_pages(page_id),"
                "word_index INTEGER NOT NULL,text TEXT NOT NULL,confidence REAL,"
                "left_px INTEGER,top_px INTEGER,width_px INTEGER,height_px INTEGER,"
                "PRIMARY KEY(page_id,word_index))"
            )
            db.execute(
                "CREATE TABLE pack_ocr_variants("
                "variant_id TEXT PRIMARY KEY,"
                "page_id TEXT NOT NULL REFERENCES pack_ocr_pages(page_id),"
                "region TEXT NOT NULL,method TEXT NOT NULL,"
                "source_bbox_pdf_json TEXT NOT NULL,"
                "candidate_text TEXT NOT NULL,"
                "crosschecked INTEGER NOT NULL CHECK(crosschecked IN (0,1)),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 CHECK(approved_for_ai=0))"
            )
            db.execute(
                "CREATE TABLE pack_ocr_issues("
                "issue_id TEXT PRIMARY KEY,"
                "page_id TEXT NOT NULL REFERENCES pack_ocr_pages(page_id),"
                "region TEXT NOT NULL,method TEXT NOT NULL,"
                "source_bbox_pdf_json TEXT NOT NULL,"
                "reason_code TEXT NOT NULL,"
                "validation_state TEXT NOT NULL DEFAULT 'unresolved' "
                "CHECK(validation_state='unresolved'))"
            )
            db.execute(
                "CREATE TABLE pack_ocr_source_items("
                "item_id TEXT PRIMARY KEY,"
                "page_id TEXT NOT NULL REFERENCES pack_ocr_pages(page_id),"
                "source_number INTEGER NOT NULL,"
                "source_text TEXT NOT NULL,"
                "source_bbox_pdf_json TEXT NOT NULL,"
                "source_method TEXT NOT NULL,"
                "validation_state TEXT NOT NULL DEFAULT 'unresolved' "
                "CHECK(validation_state='unresolved'),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 "
                "CHECK(approved_for_ai=0),"
                "UNIQUE(page_id,source_number))"
            )
            db.execute(
                "CREATE TABLE pack_ocr_numbered_segments("
                "segment_id TEXT PRIMARY KEY,"
                "page_id TEXT NOT NULL REFERENCES pack_ocr_pages(page_id),"
                "variant_id TEXT NOT NULL REFERENCES pack_ocr_variants(variant_id),"
                "side TEXT NOT NULL CHECK(side IN ('left','right')),"
                "segment_index INTEGER NOT NULL,"
                "phase TEXT NOT NULL,"
                "source_phase_heading TEXT,"
                "source_kind TEXT NOT NULL,"
                "source_bbox_scope TEXT NOT NULL "
                "CHECK(source_bbox_scope='COLUMN_ROI_ONLY'),"
                "source_bbox_pdf_json TEXT NOT NULL,"
                "heading_explicit INTEGER NOT NULL CHECK(heading_explicit IN (0,1)),"
                "review_required INTEGER NOT NULL CHECK(review_required IN (0,1)),"
                "sequence_status TEXT NOT NULL,"
                "validation_state TEXT NOT NULL DEFAULT 'unresolved' "
                "CHECK(validation_state='unresolved'),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 CHECK(approved_for_ai=0),"
                "UNIQUE(page_id,side,segment_index))"
            )
            db.execute(
                "CREATE TABLE pack_ocr_numbered_lines("
                "line_id TEXT PRIMARY KEY,"
                "segment_id TEXT NOT NULL REFERENCES pack_ocr_numbered_segments(segment_id),"
                "line_index INTEGER NOT NULL,"
                "step_number INTEGER NOT NULL,"
                "source_text TEXT NOT NULL,"
                "source_bbox_scope TEXT NOT NULL "
                "CHECK(source_bbox_scope='COLUMN_ROI_ONLY'),"
                "validation_state TEXT NOT NULL DEFAULT 'unresolved' "
                "CHECK(validation_state='unresolved'),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 CHECK(approved_for_ai=0),"
                "UNIQUE(segment_id,line_index))"
            )
            db.execute(
                "CREATE TABLE pack_ocr_page_content("
                "page_id TEXT PRIMARY KEY REFERENCES pack_ocr_pages(page_id),"
                "content_kind TEXT NOT NULL,"
                "ocr_skipped INTEGER NOT NULL CHECK(ocr_skipped IN (0,1)),"
                "nonwhite_ratio REAL,"
                "inspection_status TEXT NOT NULL,"
                "visual_review_required INTEGER NOT NULL "
                "CHECK(visual_review_required IN (0,1)),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 CHECK(approved_for_ai=0))"
            )
            if ocr_root is not None:
                directory = Path(ocr_root) / source_sha
                pages_dir = directory / "pages"
                raw_dir = directory / "raw"
                for n in range(1, expected_pages + 1):
                    pagefile = pages_dir / f"page-{n:04d}.json"
                    if not pagefile.is_file():
                        raise ValueError(f"Missing verified OCR page {n}")
                    page_data = json.loads(pagefile.read_text(encoding="utf-8"))
                    if (page_data.get("source_sha256") != source_sha or
                            int(page_data.get("page_number", -1)) != n):
                        raise ValueError(f"OCR provenance mismatch on page {n}")
                    raw = page_data.get("raw_ocr") or {}
                    payload = raw_dir / f"page-{n:04d}.tsv.gz"
                    if not payload.is_file():
                        raise ValueError(f"OCR TSV source absent for page {n}")
                    raw_gzip = payload.read_bytes()
                    if _hash(gzip.decompress(raw_gzip)) != raw.get("sha256"):
                        raise ValueError(f"OCR TSV evidence hash mismatch on page {n}")
                    member = f"ocr/page-{n:04d}.tsv.gz"
                    ocr_members[member] = raw_gzip
                    page_id = f"{revision}:p{n}"
                    db.execute(
                        "INSERT INTO pack_ocr_pages VALUES(?,?,?,?,?,?)",
                        (page_id, page_data.get("text", ""),
                         page_data.get("language"),
                         (page_data.get("confidence") or {}).get("mean"),
                         int(bool(page_data.get("review_required"))), raw["sha256"]),
                    )
                    count["ocr_pages"] += 1
                    visual = page_data.get("page_content")
                    if visual is not None:
                        if not isinstance(visual, dict):
                            raise ValueError(f"Invalid PDF visual preflight page {n}")
                        kind = visual.get("kind")
                        skipped = visual.get("ocr_skipped")
                        density = visual.get("nonwhite_ratio")
                        words_count = len(page_data.get("words") or [])
                        if (kind not in (
                                "BLANK_SCAN", "RASTER_CONTENT",
                                "SPARSE_OR_NOISE", "NATIVE_TEXT",
                            ) or type(skipped) is not bool or
                                (density is not None and (
                                    not isinstance(density, (int, float)) or
                                    not 0 <= density <= 1)) or
                                (kind == "BLANK_SCAN" and
                                 (not skipped or words_count)) or
                                (skipped and kind != "BLANK_SCAN")):
                            raise ValueError(f"Contradictory PDF visual evidence page {n}")
                        inspection_status = (
                            "CONFIRMED_BLANK_SCAN" if kind == "BLANK_SCAN"
                            else "VISUAL_CONTENT_WITHOUT_OCR" if words_count == 0
                            else "SOURCE_OCR_PRESENT"
                        )
                        db.execute(
                            "INSERT INTO pack_ocr_page_content VALUES(?,?,?,?,?,?,0)",
                            (page_id, kind, int(skipped), density,
                             inspection_status,
                             int(inspection_status == "VISUAL_CONTENT_WITHOUT_OCR")),
                        )
                        count["ocr_page_content"] += 1
                    # Enhanced OCR candidate evidence is stored independently
                    # from the original TSV. No candidate text becomes a
                    # validated technical fact merely because two modes agree.
                    variants, issues = _adaptive_ocr_evidence(page_data, n)
                    indexed_variants = {
                        region: f"{page_id}:ocrvariant:{ordinal}"
                        for ordinal, (region, *_rest) in enumerate(variants)
                    }
                    for ordinal, (region, method, bbox, candidate_text,
                                  crosschecked) in enumerate(variants):
                        db.execute(
                            "INSERT INTO pack_ocr_variants VALUES(?,?,?,?,?,?,?,0)",
                            (f"{page_id}:ocrvariant:{ordinal}", page_id,
                             region, method, json.dumps(bbox),
                             candidate_text, int(crosschecked)),
                        )
                        count["ocr_variants"] += 1
                    for ordinal, (region, method, bbox, reason_code) in enumerate(issues):
                        db.execute(
                            "INSERT INTO pack_ocr_issues VALUES(?,?,?,?,?,?,?)",
                            (f"{page_id}:ocrissue:{ordinal}", page_id, region,
                             method, json.dumps(bbox), reason_code, "unresolved"),
                        )
                        count["ocr_issues"] += 1
                    for side, segment_index, region_bbox, group in (
                            _numbered_column_evidence(page_data, n)):
                        variant = indexed_variants.get("column_" + side)
                        if variant is None:
                            raise ValueError(
                                f"Numbered OCR without source variant on page {n}"
                            )
                        segment_id = f"{page_id}:col:{side}:segment:{segment_index}"
                        db.execute(
                            "INSERT INTO pack_ocr_numbered_segments VALUES("
                            "?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                            (segment_id, page_id, variant, side, segment_index,
                             group["phase"], group.get("source_phase_heading"),
                             group["source_kind"], "COLUMN_ROI_ONLY",
                             json.dumps(region_bbox),
                             int(group["source_heading_explicit"]),
                             int(group["review_required"]),
                             group["sequence_status"], "unresolved"),
                        )
                        count["ocr_numbered_segments"] += 1
                        for line_index, item in enumerate(group["steps"]):
                            db.execute(
                                "INSERT INTO pack_ocr_numbered_lines VALUES("
                                "?,?,?,?,?,?,?,0)",
                                (f"{segment_id}:line:{line_index}",
                                 segment_id, line_index, item["number"],
                                 item["source_text"], "COLUMN_ROI_ONLY",
                                 "unresolved"),
                            )
                            count["ocr_numbered_lines"] += 1
                    anchored_items = _anchored_source_items(page_data, n)
                    for item in anchored_items:
                        number = int(item["number"])
                        db.execute(
                            "INSERT INTO pack_ocr_source_items VALUES(?,?,?,?,?,?,?,0)",
                            (f"{page_id}:source:{number}", page_id, number,
                             item["source_text"], json.dumps(item["bbox_pdf"]),
                             item["source"], "unresolved"),
                        )
                        count["ocr_source_items"] += 1
                    for ix, word in enumerate(page_data.get("words") or []):
                        db.execute(
                            "INSERT INTO pack_ocr_words VALUES(?,?,?,?,?,?,?,?)",
                            (page_id, ix, word.get("text", ""),
                             word.get("confidence"),
                             word.get("left"), word.get("top"),
                             word.get("width"), word.get("height")),
                        )
                        count["ocr_words"] += 1
            db.execute(
                "CREATE TABLE pack_procedure_continuation_candidates("
                "candidate_id TEXT PRIMARY KEY,"
                "from_page_id TEXT NOT NULL REFERENCES source_pages(page_id),"
                "to_page_id TEXT NOT NULL REFERENCES source_pages(page_id),"
                "from_original_page INTEGER NOT NULL,"
                "to_original_page INTEGER NOT NULL,"
                "candidate_repair_number TEXT NOT NULL,"
                "chapter_id TEXT NOT NULL,"
                "to_region TEXT NOT NULL,"
                "step_numbers_json TEXT NOT NULL,"
                "source_lines_json TEXT NOT NULL,"
                "evidence_json TEXT NOT NULL,"
                "validation_state TEXT NOT NULL DEFAULT 'unresolved' "
                "CHECK(validation_state='unresolved'),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 CHECK(approved_for_ai=0))"
            )
            db.execute(
                "CREATE TABLE pack_procedure_continuation_steps("
                "candidate_step_id TEXT PRIMARY KEY,"
                "candidate_id TEXT NOT NULL REFERENCES "
                "pack_procedure_continuation_candidates(candidate_id),"
                "step_number INTEGER NOT NULL,"
                "candidate_text TEXT NOT NULL,"
                "source_lines_json TEXT NOT NULL,"
                "source_line_count INTEGER NOT NULL,"
                "measurement_mentions_json TEXT NOT NULL,"
                "validation_state TEXT NOT NULL DEFAULT 'unresolved' "
                "CHECK(validation_state='unresolved'),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 "
                "CHECK(approved_for_ai=0),"
                "UNIQUE(candidate_id,step_number))"
            )
            for index, candidate in enumerate(_review_only_cross_page_links(
                fragment_root, source_sha, expected_pages,
            )):
                db.execute(
                    "INSERT INTO pack_procedure_continuation_candidates "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?, 'unresolved', 0)",
                    (f"{revision}:continuation:{index}",
                     f"{revision}:p{candidate['from_page']}",
                     f"{revision}:p{candidate['to_page']}",
                     candidate["from_original_physical_page"],
                     candidate["to_original_physical_page"],
                     candidate["candidate_repair_number"],
                     candidate["chapter_id"], candidate["to_region"],
                     json.dumps(candidate["fragment_step_numbers"]),
                     json.dumps(candidate["fragment_source_lines"]),
                     json.dumps(candidate["evidence"])),
                )
                count["procedure_continuation_candidates"] += 1
                for step in candidate["fragment_complete_steps"]:
                    db.execute(
                        "INSERT INTO pack_procedure_continuation_steps "
                        "VALUES(?,?,?,?,?,?,?,'unresolved',0)",
                        (f"{revision}:continuation:{index}:step:{step['number']}",
                         f"{revision}:continuation:{index}",
                         step["number"], step["text"],
                         json.dumps(step["source_lines"], ensure_ascii=False),
                         step["source_line_count"],
                         json.dumps(step["measurement_mentions"],
                                    ensure_ascii=False)),
                    )
                    count["procedure_continuation_steps"] += 1
            db.execute(
                "CREATE TABLE pack_source_notices("
                "notice_id TEXT PRIMARY KEY,"
                "page_id TEXT NOT NULL REFERENCES source_pages(page_id),"
                "original_physical_page INTEGER NOT NULL,"
                "source_region TEXT NOT NULL,"
                "notice_kind TEXT NOT NULL "
                "CHECK(notice_kind IN ('WARNING','CAUTION','NOTE')),"
                "source_text TEXT NOT NULL,"
                "source_lines_json TEXT NOT NULL,"
                "source_line_count INTEGER NOT NULL,"
                "relationship_to_procedure TEXT NOT NULL "
                "CHECK(relationship_to_procedure='UNRESOLVED'),"
                "validation_state TEXT NOT NULL "
                "CHECK(validation_state='unresolved'),"
                "approved_for_ai INTEGER NOT NULL DEFAULT 0 "
                "CHECK(approved_for_ai=0))"
            )
            for i, notice in enumerate(_review_only_source_notices(
                fragment_root, source_sha, expected_pages,
            )):
                db.execute(
                    "INSERT INTO pack_source_notices "
                    "VALUES(?,?,?,?,?,?,?,?,?,'unresolved',0)",
                    (f"{revision}:notice:{i}",
                     f"{revision}:p{notice['page_number']}",
                     notice["original_physical_page"], notice["region"],
                     notice["kind"], notice["source_text"],
                     json.dumps(notice["source_lines"], ensure_ascii=False),
                     notice["source_line_count"], "UNRESOLVED"),
                )
                count["safety_notice_candidates"] += 1
            db.execute(
                "CREATE TABLE pack_image_occurrences("
                "revision_id TEXT NOT NULL,page_number INTEGER NOT NULL,"
                "image_index INTEGER NOT NULL,sha256_or_digest TEXT,"
                "bbox_json TEXT, width INTEGER,height INTEGER,"
                "PRIMARY KEY(revision_id,page_number,image_index))"
            )
            for visual in records(data_root / "images.jsonl.gz"):
                db.execute(
                    "INSERT INTO pack_image_occurrences VALUES(?,?,?,?,?,?,?)",
                    (revision, visual["page_number"], visual["image_index"],
                     visual.get("digest"), json.dumps(visual.get("bbox")),
                     visual.get("width"), visual.get("height")),
                )
                count["image_occurrences"] += 1
            for item in records(data_root / "review_items.jsonl.gz"):
                n = int(item["page_number"])
                db.execute(
                    "INSERT INTO review_items(review_id,revision_id,page_id,severity,"
                    "reason_code,reason_text,status) VALUES(?,?,?,?,?,?,?)",
                    (item["review_id"], revision, f"{revision}:p{n}",
                     item["severity"], item["code"],
                     item["reason"],
                     "pending"),
                )
                count["reviews"] += 1
            db.execute(
                "INSERT OR REPLACE INTO database_metadata(key,value) VALUES"
                "('pack_kind','raw_document_candidate'),"
                "('publication_state','blocked_semantic_review')"
            )
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Candidate SQLite failed integrity check")
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Candidate SQLite foreign key violations")
            db.commit()
        # Even native-text PDFs still need semantic/applicability review.
        # Their raw text can be searched without asserting technical validity.
        pdf_bytes = pdf_path.read_bytes()
        db_bytes = database_file.read_bytes()
        manifest = {
            "format": "mems-forge-document-pack", "format_version": 1,
            "forge_version": __version__,
            "pack_id": "DOC_" + source_sha[:24],
            "source": {
                "filename": pdf_path.name, "sha256": source_sha,
                "size_bytes": len(pdf_bytes), "pages": expected_pages,
            },
            "members_sha256": {
                "source/document.pdf": _hash(pdf_bytes),
                "data/knowledge.sqlite": _hash(db_bytes),
            },
            "counts": count,
            "publication_state": "CANDIDATE_NOT_VALIDATED",
            "reason": (
                "Native text and visual occurrence inventory only; "
                "semantic/applicability validation not complete"
            ),
            "blocking_reader_reviews": summary["counts"]["blocking_review_items"],
            "available_for": ["direct_raw_search", "source_pdf_viewer"],
            "not_approved_for": ["validated_technical_facts", "automatic_ai_answers"],
        }
        if count["ocr_pages"]:
            manifest["available_for"].append("direct_ocr_text_search_with_provenance")
            for member, content in sorted(ocr_members.items()):
                manifest["members_sha256"][member] = _hash(content)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise FileExistsError(f"Pack exists: {output_path}")
        entries = {
            "manifest.json": (json.dumps(
                manifest, sort_keys=True, ensure_ascii=False, indent=2
            ) + "\n").encode("utf-8"),
            "source/document.pdf": pdf_bytes,
            "data/knowledge.sqlite": db_bytes,
            **ocr_members,
        }
        with zipfile.ZipFile(output_path, "x", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as pack:
            for filename in sorted(entries):
                info = zipfile.ZipInfo(filename, date_time=(2024, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                pack.writestr(info, entries[filename])
        checked = inspect_document_pack(output_path)
        if not checked["valid"]:
            output_path.unlink(missing_ok=True)
            raise ValueError("Candidate package verification failed: " + str(checked))
        return {"path": str(output_path), "manifest": manifest, "validation": checked}


def inspect_document_pack(path: Path) -> dict:
    """Validate a standalone candidate without extracting files or modifying SQLite."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            expected = {"manifest.json", "source/document.pdf", "data/knowledge.sqlite"}
            expected.update(manifest_name for manifest_name in archive.namelist()
                            if manifest_name.startswith("ocr/"))
            if set(archive.namelist()) != expected:
                return {"valid": False, "error": "unexpected_or_missing_members"}
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "mems-forge-document-pack":
                return {"valid": False, "error": "unknown_format"}
            if manifest.get("format_version") != 1:
                return {"valid": False, "error": "unknown_format_version"}
            if set(manifest["members_sha256"]) != expected - {"manifest.json"}:
                return {"valid": False, "error": "manifest_member_list_mismatch"}
            for member, expected_sha in manifest["members_sha256"].items():
                if member not in expected or _hash(archive.read(member)) != expected_sha:
                    return {"valid": False, "error": "member_hash_invalid", "member": member}
            if _hash(archive.read("source/document.pdf")) != manifest["source"]["sha256"]:
                return {"valid": False, "error": "source_sha256_invalid"}
            with tempfile.TemporaryDirectory(prefix="mems-forge-verify-") as tmp:
                db_file = Path(tmp) / "candidate.sqlite"
                db_file.write_bytes(archive.read("data/knowledge.sqlite"))
                with closing(sqlite3.connect(db_file)) as db:
                    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
                    pages = db.execute("SELECT COUNT(*) FROM source_pages").fetchone()[0]
                    ocr_pages = db.execute("SELECT COUNT(*) FROM pack_ocr_pages").fetchone()[0]
                    # Format-v1 packs issued before adaptive OCR had no
                    # variants table; they remain readable and unapproved.
                    table_present = db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='pack_ocr_variants'"
                    ).fetchone() is not None
                    ocr_variants = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_variants"
                    ).fetchone()[0] if table_present else 0)
                    issue_table_present = db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='pack_ocr_issues'"
                    ).fetchone() is not None
                    ocr_issues = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_issues"
                    ).fetchone()[0] if issue_table_present else 0)
                    anchored_table_present = db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='pack_ocr_source_items'"
                    ).fetchone() is not None
                    ocr_source_items = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_source_items"
                    ).fetchone()[0] if anchored_table_present else 0)
                    bad_source_items = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_source_items "
                        "WHERE approved_for_ai<>0 OR validation_state<>'unresolved'"
                    ).fetchone()[0] if anchored_table_present else 0)
                    has_numbered_index = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='pack_ocr_numbered_segments'"
                    ).fetchone() is not None
                    numbered_segments = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_numbered_segments"
                    ).fetchone()[0] if has_numbered_index else 0)
                    numbered_lines = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_numbered_lines"
                    ).fetchone()[0] if has_numbered_index else 0)
                    invalid_numbered_evidence = (
                        db.execute(
                            "SELECT COUNT(*) FROM pack_ocr_numbered_segments "
                            "WHERE validation_state<>'unresolved' "
                            "OR approved_for_ai<>0 OR "
                            "(heading_explicit=0 AND "
                            "(phase<>'unassigned' OR review_required<>1))"
                        ).fetchone()[0] +
                        db.execute(
                            "SELECT COUNT(*) FROM pack_ocr_numbered_lines "
                            "WHERE validation_state<>'unresolved' OR approved_for_ai<>0"
                        ).fetchone()[0]
                    ) if has_numbered_index else 0
                    # No step-level source location is known, only a column ROI.
                    unsupported_positions = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_numbered_lines "
                        "WHERE source_bbox_scope<>'COLUMN_ROI_ONLY'"
                    ).fetchone()[0] if has_numbered_index else 0)
                    if has_numbered_index:
                        rows = db.execute(
                            "SELECT s.segment_id,s.side,s.segment_index,s.phase,"
                            "s.source_phase_heading,s.source_kind,"
                            "s.source_bbox_scope,s.source_bbox_pdf_json,"
                            "s.heading_explicit,s.review_required,s.sequence_status,"
                            "s.page_id,v.page_id,v.region,v.candidate_text,"
                            "v.source_bbox_pdf_json "
                            "FROM pack_ocr_numbered_segments s "
                            "JOIN pack_ocr_variants v ON s.variant_id=v.variant_id"
                        ).fetchall()
                        for (segment_id, side, pos, phase, heading, kind, scope,
                             region_bbox, has_heading, review, sequence, page_id,
                             variant_page, variant_region, variant_text,
                             variant_bbox) in rows:
                            expected_groups = _phase_bounded_numbered_steps(variant_text)
                            if (page_id != variant_page or
                                    variant_region != "column_" + side or
                                    scope != "COLUMN_ROI_ONLY" or
                                    json.loads(region_bbox) != json.loads(variant_bbox) or
                                    pos < 0 or pos >= len(expected_groups)):
                                return {
                                    "valid": False,
                                    "error": "ocr_numbered_source_provenance_invalid",
                                }
                            group = expected_groups[pos]
                            if (phase != group["phase"] or
                                    heading != group.get("source_phase_heading") or
                                    kind != group["source_kind"] or
                                    has_heading != int(group["source_heading_explicit"]) or
                                    review != int(group["review_required"]) or
                                    sequence != group["sequence_status"]):
                                return {
                                    "valid": False,
                                    "error": "ocr_numbered_segment_not_from_source",
                                }
                            stored_lines = db.execute(
                                "SELECT line_index,step_number,source_text,"
                                "source_bbox_scope FROM pack_ocr_numbered_lines "
                                "WHERE segment_id=? ORDER BY line_index",
                                (segment_id,),
                            ).fetchall()
                            expected_lines = [
                                (i, line["number"], line["source_text"],
                                 "COLUMN_ROI_ONLY")
                                for i, line in enumerate(group["steps"])
                            ]
                            if stored_lines != expected_lines:
                                return {
                                    "valid": False,
                                    "error": "ocr_numbered_line_not_from_source",
                                }
                    visual_status_table = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='pack_ocr_page_content'"
                    ).fetchone() is not None
                    visual_pages = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_page_content"
                    ).fetchone()[0] if visual_status_table else 0)
                    malformed_visual = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_page_content v "
                        "WHERE approved_for_ai<>0 OR "
                        "(content_kind='BLANK_SCAN' AND "
                        "(ocr_skipped<>1 OR inspection_status<>'CONFIRMED_BLANK_SCAN')) "
                        "OR (inspection_status='VISUAL_CONTENT_WITHOUT_OCR' "
                        "AND visual_review_required<>1)"
                    ).fetchone()[0] if visual_status_table else 0)
                    if visual_status_table:
                        for (visual_page_id, kind, skipped, status, review) in db.execute(
                            "SELECT page_id,content_kind,ocr_skipped,"
                            "inspection_status,visual_review_required "
                            "FROM pack_ocr_page_content"
                        ):
                            word_total = db.execute(
                                "SELECT COUNT(*) FROM pack_ocr_words "
                                "WHERE page_id=?", (visual_page_id,)
                            ).fetchone()[0]
                            required_status = (
                                "CONFIRMED_BLANK_SCAN" if kind == "BLANK_SCAN"
                                else "VISUAL_CONTENT_WITHOUT_OCR" if word_total == 0
                                else "SOURCE_OCR_PRESENT"
                            )
                            if (status != required_status or
                                    review != int(
                                        status == "VISUAL_CONTENT_WITHOUT_OCR") or
                                    (kind == "BLANK_SCAN" and word_total != 0) or
                                    (kind != "BLANK_SCAN" and skipped)):
                                return {
                                    "valid": False,
                                    "error": "ocr_visual_status_contradicts_source",
                                }
                    has_continuation_index = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='pack_procedure_continuation_candidates'"
                    ).fetchone() is not None
                    continuation_count = (db.execute(
                        "SELECT COUNT(*) FROM pack_procedure_continuation_candidates"
                    ).fetchone()[0] if has_continuation_index else 0)
                    invalid_continuations = (db.execute(
                        "SELECT COUNT(*) FROM pack_procedure_continuation_candidates c "
                        "JOIN source_pages p ON c.from_page_id=p.page_id "
                        "JOIN source_pages q ON c.to_page_id=q.page_id "
                        "WHERE c.validation_state<>'unresolved' OR c.approved_for_ai<>0 "
                        "OR q.physical_page_number<>p.physical_page_number+1 "
                        "OR c.to_original_page<>c.from_original_page+1"
                    ).fetchone()[0] if has_continuation_index else 0)
                    has_notice_index = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='pack_source_notices'"
                    ).fetchone() is not None
                    notice_count = (db.execute(
                        "SELECT COUNT(*) FROM pack_source_notices"
                    ).fetchone()[0] if has_notice_index else 0)
                    invalid_notices = (db.execute(
                        "SELECT COUNT(*) FROM pack_source_notices "
                        "WHERE validation_state<>'unresolved' OR approved_for_ai<>0 "
                        "OR relationship_to_procedure<>'UNRESOLVED' OR "
                        "source_line_count<1"
                    ).fetchone()[0] if has_notice_index else 0)
                    has_step_index = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='pack_procedure_continuation_steps'"
                    ).fetchone() is not None
                    continuity_steps = (db.execute(
                        "SELECT COUNT(*) FROM pack_procedure_continuation_steps"
                    ).fetchone()[0] if has_step_index else 0)
                    invalid_steps = (db.execute(
                        "SELECT COUNT(*) FROM pack_procedure_continuation_steps "
                        "WHERE validation_state<>'unresolved' OR approved_for_ai<>0 "
                        "OR source_line_count<1 OR step_number<1"
                    ).fetchone()[0] if has_step_index else 0)
                    incorrectly_approved = (db.execute(
                        "SELECT COUNT(*) FROM pack_ocr_variants WHERE approved_for_ai<>0"
                    ).fetchone()[0] if table_present else 0)
                    metadata = db.execute(
                        "SELECT value FROM database_metadata WHERE key='publication_state'"
                    ).fetchone()
                    source_line_index_by_page = {}
                    indexed_notice_pages = (set(row[0] for row in db.execute(
                        "SELECT DISTINCT q.physical_page_number "
                        "FROM pack_source_notices n "
                        "JOIN source_pages q ON n.page_id=q.page_id"
                    )) if has_notice_index else set())
                    indexed_link_pages = (set(row[0] for row in db.execute(
                        "SELECT DISTINCT q.physical_page_number "
                        "FROM pack_procedure_continuation_candidates c "
                        "JOIN source_pages q ON c.to_page_id=q.page_id"
                    )) if has_continuation_index else set())
                    # Compare immutable OCR TSV with BOTH the SQLite words
                    # and the searchable text. Checksums alone are insufficient
                    # if one layer was generated from an unrelated OCR result.
                    for n in range(1, pages + 1):
                        page_id = "REV_" + manifest["source"]["sha256"] + f":p{n}"
                        indexed_page = db.execute(
                            "SELECT text,raw_tsv_sha256 FROM pack_ocr_pages "
                            "WHERE page_id=?", (page_id,),
                        ).fetchone()
                        if indexed_page is None:
                            continue
                        member = f"ocr/page-{n:04d}.tsv.gz"
                        if member not in archive.namelist():
                            return {"valid": False, "error": "ocr_source_tsv_missing", "page": n}
                        original_tsv = gzip.decompress(archive.read(member))
                        if _hash(original_tsv) != indexed_page[1]:
                            return {"valid": False, "error": "ocr_source_tsv_sha256_mismatch",
                                    "page": n}
                        parsed, warnings = _parse_words(
                            original_tsv.decode("utf-8"), n
                        )
                        if n in indexed_link_pages or n in indexed_notice_pages:
                            source_line_index_by_page[n] = _immutable_ocr_line_index(
                                parsed
                            )
                        if warnings:
                            return {"valid": False, "error": "ocr_source_tsv_parse_warning",
                                    "page": n}
                        if _words_to_text(parsed) != indexed_page[0]:
                            return {"valid": False, "error": "ocr_text_not_from_source_tsv",
                                    "page": n}
                        indexed_words = db.execute(
                            "SELECT text,confidence,left_px,top_px,width_px,height_px "
                            "FROM pack_ocr_words WHERE page_id=? ORDER BY word_index",
                            (page_id,),
                        ).fetchall()
                        expected_words = [
                            (word.text, word.confidence, word.left, word.top,
                             word.width, word.height) for word in parsed
                        ]
                        if indexed_words != expected_words:
                            return {"valid": False, "error": "ocr_words_not_from_source_tsv",
                                    "page": n}
                    if has_notice_index:
                        for (page_n, kind, content, ref_json, count,
                             relation, state, approved) in db.execute(
                            "SELECT p.physical_page_number,n.notice_kind,"
                            "n.source_text,n.source_lines_json,"
                            "n.source_line_count,n.relationship_to_procedure,"
                            "n.validation_state,n.approved_for_ai "
                            "FROM pack_source_notices n JOIN source_pages p "
                            "ON n.page_id=p.page_id"
                        ):
                            try:
                                refs = json.loads(ref_json)
                            except (TypeError, ValueError):
                                return {
                                    "valid": False,
                                    "error": "source_notice_bad_lineage",
                                }
                            if (not isinstance(refs, list) or not refs
                                    or len(refs) != count
                                    or content != " ".join(
                                        str(ref.get("text", "")) for ref in refs
                                    ) or not re.match(
                                        rf"^{kind}\s*:",
                                        str(refs[0].get("text", "")),
                                        re.IGNORECASE,
                                    ) or relation != "UNRESOLVED"
                                    or state != "unresolved" or approved != 0):
                                return {
                                    "valid": False,
                                    "error": "source_notice_not_preserved",
                                }
                            canonical = source_line_index_by_page.get(page_n, {})
                            for source_line in refs:
                                key = source_line.get("source_line_key")
                                if (not isinstance(key, list) or len(key) != 3
                                        or any(type(x) is not int for x in key)):
                                    return {
                                        "valid": False,
                                        "error": "source_notice_bad_lineage",
                                    }
                                official = canonical.get(tuple(key))
                                if (official is None or
                                        official["text"] != source_line.get("text") or
                                        official["bbox"] != source_line.get("bbox")):
                                    return {
                                        "valid": False,
                                        "error": "source_notice_not_from_original_tsv",
                                    }
                    if has_step_index:
                        candidates = db.execute(
                            "SELECT c.candidate_id,q.physical_page_number,"
                            "c.step_numbers_json,c.source_lines_json "
                            "FROM pack_procedure_continuation_candidates c "
                            "JOIN source_pages q ON c.to_page_id=q.page_id"
                        ).fetchall()
                        for candidate_id, page_n, numbers_json, starters_json in candidates:
                            numbers = json.loads(numbers_json)
                            starters = json.loads(starters_json)
                            full_steps = db.execute(
                                "SELECT step_number,candidate_text,source_lines_json,"
                                "source_line_count,measurement_mentions_json "
                                "FROM pack_procedure_continuation_steps "
                                "WHERE candidate_id=? ORDER BY step_number",
                                (candidate_id,),
                            ).fetchall()
                            if (len(full_steps) != len(numbers)
                                    or len(starters) != len(numbers)):
                                return {
                                    "valid": False,
                                    "error": "multiline_step_count_mismatch",
                                }
                            immutable = source_line_index_by_page.get(page_n)
                            for i, (number, text, sources_json, count, units_json) in enumerate(full_steps):
                                sources = json.loads(sources_json)
                                unit_mentions = json.loads(units_json)
                                if (number != numbers[i] or not sources
                                        or len(sources) != count
                                        or sources[0] != starters[i]
                                        or unit_mentions != _measured_value_candidates(text)):
                                    return {
                                        "valid": False,
                                        "error": "multiline_step_provenance_inconsistent",
                                    }
                                start = STEP_RE.match(str(sources[0].get("text", "")))
                                if start is None or int(start.group(1)) != number:
                                    return {
                                        "valid": False,
                                        "error": "multiline_step_source_number_mismatch",
                                    }
                                reconstructed = _normalise_space(" ".join(
                                    [start.group(2)] +
                                    [source_line.get("text", "") for source_line in sources[1:]]
                                ))
                                if reconstructed != text:
                                    return {
                                        "valid": False,
                                        "error": "multiline_step_text_not_from_source",
                                    }
                                for origin in sources:
                                    line_key = origin.get("source_line_key")
                                    bbox = origin.get("bbox")
                                    if (not isinstance(line_key, list)
                                            or len(line_key) != 3
                                            or any(type(x) is not int for x in line_key)
                                            or not isinstance(bbox, list)
                                            or len(bbox) != 4):
                                        return {
                                            "valid": False,
                                            "error": "multiline_step_lineage_invalid",
                                        }
                                    if immutable is not None:
                                        canonical = immutable.get(tuple(line_key))
                                        if (canonical is None
                                                or canonical["text"] != origin["text"]
                                                or canonical["bbox"] != bbox):
                                            return {
                                                "valid": False,
                                                "error": "multiline_step_not_from_original_tsv",
                                            }
                    if (integrity != "ok" or
                            db.execute("PRAGMA foreign_key_check").fetchall() or
                            pages != manifest["source"]["pages"] or
                            ocr_pages != manifest["counts"].get("ocr_pages", 0) or
                            ocr_variants != manifest["counts"].get("ocr_variants", 0) or
                            ocr_issues != manifest["counts"].get("ocr_issues", 0) or
                            ocr_source_items != manifest["counts"].get("ocr_source_items", 0) or
                            bad_source_items or
                            numbered_segments != manifest["counts"].get("ocr_numbered_segments", 0) or
                            numbered_lines != manifest["counts"].get("ocr_numbered_lines", 0) or
                            invalid_numbered_evidence or unsupported_positions or
                            visual_pages != manifest["counts"].get("ocr_page_content", 0) or
                            malformed_visual or
                            continuation_count != manifest["counts"].get("procedure_continuation_candidates", 0) or
                            invalid_continuations or
                            continuity_steps != manifest["counts"].get("procedure_continuation_steps", 0) or
                            invalid_steps or
                            notice_count != manifest["counts"].get("safety_notice_candidates", 0) or
                            invalid_notices or
                            incorrectly_approved or
                            metadata is None or metadata[0] != "blocked_semantic_review"):
                        return {"valid": False, "error": "sqlite_validation_failed"}
            return {
                "valid": True, "pack_id": manifest["pack_id"],
                "source_pages": manifest["source"]["pages"],
                "state": manifest["publication_state"],
            }
    except (OSError, ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
        return {"valid": False, "error": type(exc).__name__ + ": " + str(exc)}
