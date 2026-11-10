"""Build *unapproved* technical candidates from proven OCR line anchors.

Only the document's own geometry is trusted. This module never guesses a
vehicle/ECU applicability, a continuation from the other page column, or a
measurement belonging to a neighbouring procedure. Such interpretation needs
separate review before release.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3


_REPAIR_NUMBER = re.compile(r"^[0-9]{2}\.[0-9]{2}\.[0-9]{2}$")
_WORDS = re.compile(r"[\w]+", re.UNICODE)
_VERSION = "source-anchored-repair-reference-v1"


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in _WORDS.findall(text)]


def _anchor(db: sqlite3.Connection, page_id: str, evidence: dict,
            *, scale_x: float, scale_y: float) -> dict:
    """Verify OCR words at the exact region before recording any candidate."""
    if not isinstance(evidence, dict):
        raise ValueError("MISSING_GEOMETRIC_EVIDENCE")
    box = evidence.get("bbox")
    text = evidence.get("text")
    if not isinstance(box, list) or len(box) != 4 or not isinstance(text, str):
        raise ValueError("INVALID_GEOMETRIC_EVIDENCE")
    x0, y0, x1, y1 = (float(v) for v in box)
    if not (0 <= x0 < x1 and 0 <= y0 < y1):
        raise ValueError("INVALID_EVIDENCE_RECTANGLE")
    if scale_x <= 0 or scale_y <= 0:
        raise ValueError("INVALID_OCR_COORDINATE_SCALE")
    words = db.execute(
        "SELECT text,confidence FROM pack_ocr_words WHERE page_id=? "
        "AND left_px+width_px/2.0 BETWEEN ? AND ? "
        "AND top_px+height_px/2.0 BETWEEN ? AND ? "
        "ORDER BY left_px,word_index",
        (page_id, x0-3, x1+3, y0-3, y1+3),
    ).fetchall()
    observed = " ".join(str(word[0] or "") for word in words)
    if not _tokens(text) or _tokens(text) != _tokens(observed):
        raise ValueError("OCR_EVIDENCE_TEXT_OR_POSITION_MISMATCH")
    confidence = [float(word[1]) for word in words if word[1] is not None]
    return {
        "text": text, "bbox_pdf": (x0/scale_x, y0/scale_y,
                                  x1/scale_x, y1/scale_y),
        "confidence": min(confidence)/100.0 if confidence else None,
        "line_key": evidence.get("source_line_key"),
        "region": evidence.get("region"),
    }


def register_repair_candidates(
    db: sqlite3.Connection, structure_root: Path, ocr_root: Path,
    source_sha256: str, page_count: int,
) -> dict:
    """Populate candidate-only fact/provenance/review tables, fail closed.

    Every repair-number and title pair has two independently rechecked OCR
    anchors in PDF-page coordinates. Review status cannot become approved here.
    """
    structured = Path(structure_root) / source_sha256 / "pages"
    ocr_pages = Path(ocr_root) / source_sha256 / "pages"
    if not structured.is_dir() or not ocr_pages.is_dir():
        raise ValueError("FULL_STRUCTURE_AND_OCR_REQUIRED")
    count = {"structured_candidates": 0, "candidate_provenance": 0,
             "structured_reviews": 0, "ambiguous_sequences": 0}
    seen = set()
    for number in range(1, page_count + 1):
        file = structured / f"page-{number:04d}.json"
        ocr_file = ocr_pages / f"page-{number:04d}.json"
        if not file.is_file() or not ocr_file.is_file():
            raise ValueError(f"STRUCTURE_OR_OCR_PAGE_MISSING:{number}")
        structure = json.loads(file.read_text(encoding="utf-8"))
        ocr = json.loads(ocr_file.read_text(encoding="utf-8"))
        if (int(structure.get("page_number", -1)) != number or
                int(ocr.get("page_number", -1)) != number or
                ocr.get("source_sha256") != source_sha256):
            raise ValueError(f"STRUCTURE_SOURCE_PAGE_MISMATCH:{number}")
        coords = ocr.get("coordinate_space") or {}
        scale_x = float(coords.get("pdf_to_ocr_scale_x", 0))
        scale_y = float(coords.get("pdf_to_ocr_scale_y", 0))
        page_id = f"REV_{source_sha256}:p{number}"
        for ordinal, item in enumerate(structure.get("procedures") or [], 1):
            if int(item.get("page_number", -1)) != number:
                raise ValueError(f"PROCEDURE_PAGE_MISMATCH:{number}")
            if item.get("status") != "REVIEW_REQUIRED":
                raise ValueError("UNREVIEWED_PROCEDURE_CLAIMS_APPROVAL")
            title = str(item.get("title") or "").strip()
            repair_number = str(item.get("repair_number") or "").strip()
            if not title or not _REPAIR_NUMBER.fullmatch(repair_number):
                raise ValueError(f"INCOMPLETE_REPAIR_IDENTITY:{number}:{ordinal}")
            title_source = _anchor(db, page_id, item.get("title_source"),
                                   scale_x=scale_x, scale_y=scale_y)
            number_source = _anchor(db, page_id, item.get("repair_number_source"),
                                    scale_x=scale_x, scale_y=scale_y)
            region = str(item.get("region") or "")
            if (region not in {"left", "right", "body"} or
                    title_source["region"] != region or
                    number_source["region"] != region or
                    repair_number not in number_source["text"]):
                raise ValueError(f"COLUMN_OR_NUMBER_MISMATCH:{number}:{ordinal}")
            identifier = f"repairref-{source_sha256[:20]}-{number:04d}-{ordinal:03d}"
            if identifier in seen:
                raise ValueError("REPEATED_STRUCTURED_CANDIDATE_ID")
            seen.add(identifier)
            flags = list(item.get("review_reasons") or [])
            sequence = str(item.get("step_sequence_status") or "")
            if sequence not in {"contiguous", "contiguous_by_phase", "single_step"}:
                flags.append("STEP_SEQUENCE_REQUIRES_REVIEW")
                count["ambiguous_sequences"] += 1
            if not flags:
                flags.append("SEMANTIC_APPLICABILITY_NOT_REVIEWED")
            payload = {
                "repair_number": repair_number, "title_candidate": title,
                "document_page": number, "column_region": region,
                "sequence_status": sequence,
                "evidence_title": title_source["text"],
                "evidence_repair_number": number_source["text"],
                "review_reasons": sorted(set(flags)),
                "confidence_kind": "OCR_EXTRACTION_ONLY_NOT_TECHNICAL_VALIDITY",
                "applicability": "NOT_ESTABLISHED",
                "automatic_ai_answers": False,
            }
            db.execute(
                "INSERT INTO structured_entities("
                "entity_id,entity_type,validation_state,parser_version,canonical_key,payload_json)"
                " VALUES(?,?,?,?,?,?)",
                (identifier, "repair_reference_candidate", "unresolved", _VERSION,
                 f"{source_sha256}:p{number}:{region}:{repair_number}",
                 json.dumps(payload,sort_keys=True,ensure_ascii=False)),
            )
            for role, anchor in (("title", title_source), ("repair_number", number_source)):
                x0,y0,x1,y1 = anchor["bbox_pdf"]
                db.execute(
                    "INSERT INTO provenance("
                    "provenance_id,entity_id,revision_id,page_id,"
                    "bbox_x0,bbox_y0,bbox_x1,bbox_y1,evidence_text,confidence)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (identifier+":"+role, identifier, "REV_"+source_sha256, page_id,
                     x0,y0,x1,y1,anchor["text"],anchor["confidence"]),
                )
                count["candidate_provenance"] += 1
            db.execute(
                "INSERT INTO review_items("
                "review_id,entity_id,revision_id,page_id,severity,reason_code,"
                "reason_text,status) VALUES(?,?,?,?,?,?,?,?)",
                ("review:"+identifier,identifier,"REV_"+source_sha256,page_id,
                 "blocking","STRUCTURED_TECHNICAL_VALIDATION",
                 "Original image, full title, repair number, step association and "
                 "vehicle/ECU applicability must be verified before any publication",
                 "pending"),
            )
            count["structured_reviews"] += 1
            count["structured_candidates"] += 1
    return count
