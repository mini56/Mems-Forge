from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from typing import Any

HIERARCHY_VERSION = "0.1.0"

_GENERIC_CONTENT_HEADINGS = {
    "CONTENTS",
}
_CONTENT_GROUPS = {
    "DESCRIPTION",
    "DESCRIPTION AND OPERATION",
    "ADJUSTMENT",
    "ADJUSTMENTS",
    "REPAIR",
    "REPAIRS",
    "GENERAL INFORMATION",
    "WIRE HARNESS AND EARTH LOCATIONS",
}


def _normalise_space(text: str) -> str:
    return " ".join(str(text).replace("\u2013", "-").replace("\u2014", "-").split())


def _letters(text: str) -> str:
    return "".join(ch for ch in text if ch.isalpha())


def _clean_heading(text: str) -> str:
    text = _normalise_space(text).strip()
    text = re.sub(r"^[^A-Za-z0-9]+", "", text)
    text = re.sub(r"[^A-Za-z0-9)./&+-]+$", "", text)
    return text.strip(" .:-|")


def _heading_like(text: str) -> bool:
    text = _clean_heading(text)
    letters = _letters(text)
    if len(letters) < 4 or len(text) > 110:
        return False
    if text.upper() in _GENERIC_CONTENT_HEADINGS:
        return False
    upper_ratio = sum(ch.isupper() for ch in letters) / max(1, len(letters))
    return upper_ratio >= 0.72


def _line_ref(line: dict[str, Any], *, page_number: int) -> dict[str, Any]:
    return {
        "physical_page_number": page_number,
        "region": line.get("region"),
        "source_line_key": line.get("source_line_key"),
        "bbox": line.get("bbox"),
        "mean_confidence": line.get("mean_confidence"),
        "text": line.get("text", ""),
    }


def _title_from_contents_or_following_page(
    page_number: int,
    layout_by_page: dict[int, dict[str, Any]],
    *,
    max_forward_pages: int = 4,
) -> dict[str, Any]:
    page = layout_by_page[page_number]
    for line in page.get("lines", []):
        if str(line.get("region")) != "header":
            continue
        text = _clean_heading(str(line.get("text", "")))
        if _heading_like(text):
            return {
                "title": text,
                "method": "contents_page_header",
                "status": "CANDIDATE_ONLY",
                "evidence": _line_ref(line, page_number=page_number),
            }

    for following in range(page_number + 1, min(max(layout_by_page), page_number + max_forward_pages) + 1):
        candidate_page = layout_by_page.get(following)
        if candidate_page is None:
            continue
        for line in candidate_page.get("lines", []):
            if str(line.get("region")) != "header":
                continue
            text = _clean_heading(str(line.get("text", "")))
            if _heading_like(text):
                return {
                    "title": text,
                    "method": "nearby_following_page_header",
                    "status": "REVIEW_REQUIRED",
                    "evidence": _line_ref(line, page_number=following),
                }

    return {
        "title": None,
        "method": "unresolved",
        "status": "REVIEW_REQUIRED",
        "evidence": None,
    }


def _stable_slug(text: str | None) -> str:
    if not text:
        return "unresolved"
    value = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "unresolved"


def _contents_line_role(text: str) -> str:
    clean = _normalise_space(text).strip(" .:-")
    return "group_heading_candidate" if clean.upper() in _CONTENT_GROUPS else "entry_candidate"


def _contents_entry(line: dict[str, Any], *, page_number: int, group: str | None) -> dict[str, Any]:
    raw = _normalise_space(str(line.get("text", "")))
    page_match = re.search(r"\s+(\d{1,3})\s*$", raw)
    printed_page = int(page_match.group(1)) if page_match else None
    title_candidate = raw[: page_match.start()].rstrip(" .:-") if page_match else raw
    alpha = sum(ch.isalpha() for ch in title_candidate)
    nonspace = sum(not ch.isspace() for ch in title_candidate)
    noise_ratio = 1.0 - (alpha / max(1, nonspace))
    return {
        "role": "contents_entry_candidate",
        "status": "REVIEW_REQUIRED" if noise_ratio > 0.45 else "CANDIDATE_ONLY",
        "group": group,
        "raw_text": raw,
        "title_candidate": title_candidate,
        "printed_page_candidate": printed_page,
        "noise_ratio": noise_ratio,
        "source": _line_ref(line, page_number=page_number),
    }


def _parse_contents_page(page_number: int, layout: dict[str, Any]) -> list[dict[str, Any]]:
    lines = list(layout.get("lines", []))
    contents_index = None
    for index, line in enumerate(lines):
        text = _normalise_space(str(line.get("text", ""))).strip(" .:-").upper()
        if text == "CONTENTS":
            contents_index = index
            break
    if contents_index is None:
        return []

    entries: list[dict[str, Any]] = []
    current_group: str | None = None
    for line in lines[contents_index + 1 :]:
        region = str(line.get("region") or "")
        if region in {"header", "footer"}:
            continue
        text = _normalise_space(str(line.get("text", ""))).strip()
        if not text:
            continue
        role = _contents_line_role(text)
        if role == "group_heading_candidate":
            current_group = text.strip(" .:-")
            entries.append(
                {
                    "role": role,
                    "status": "CANDIDATE_ONLY",
                    "group": current_group,
                    "raw_text": text,
                    "source": _line_ref(line, page_number=page_number),
                }
            )
            continue
        entries.append(_contents_entry(line, page_number=page_number, group=current_group))
    return entries


def build_document_hierarchy(
    layout_pages: list[dict[str, Any]],
    structure_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    layout_by_page = {int(page["page_number"]): page for page in layout_pages}
    structure_by_page = {int(page["page_number"]): page for page in structure_pages}
    if set(layout_by_page) != set(structure_by_page):
        raise ValueError("layout/structure page sets differ")
    if not layout_by_page:
        raise ValueError("no pages")

    anchor_pages = [
        page_number
        for page_number in sorted(structure_by_page)
        if structure_by_page[page_number].get("page_class") == "contents_page_candidate"
    ]

    anchors: list[dict[str, Any]] = []
    for page_number in anchor_pages:
        title = _title_from_contents_or_following_page(page_number, layout_by_page)
        anchors.append(
            {
                "physical_page_number": page_number,
                "title_candidate": title["title"],
                "title_detection_method": title["method"],
                "status": title["status"],
                "title_evidence": title["evidence"],
                "contents_entries": _parse_contents_page(page_number, layout_by_page[page_number]),
            }
        )

    chapters: list[dict[str, Any]] = []
    for anchor in anchors:
        title = anchor["title_candidate"]
        normalised = _normalise_space(title or "").casefold()
        if chapters and title and chapters[-1]["title_candidate"] and _normalise_space(chapters[-1]["title_candidate"]).casefold() == normalised:
            chapters[-1]["contents_pages"].append(anchor["physical_page_number"])
            chapters[-1]["anchor_evidence"].append(anchor)
            if anchor["status"] == "REVIEW_REQUIRED":
                chapters[-1]["status"] = "REVIEW_REQUIRED"
            continue
        chapters.append(
            {
                "chapter_id": f"chapter-{anchor['physical_page_number']:04d}-{_stable_slug(title)}",
                "kind": "manufacturer_chapter_candidate",
                "status": anchor["status"],
                "title_candidate": title,
                "start_physical_page": anchor["physical_page_number"],
                "end_physical_page": None,
                "contents_pages": [anchor["physical_page_number"]],
                "anchor_evidence": [anchor],
            }
        )

    max_page = max(layout_by_page)
    for index, chapter in enumerate(chapters):
        chapter["end_physical_page"] = (
            chapters[index + 1]["start_physical_page"] - 1 if index + 1 < len(chapters) else max_page
        )
        chapter["range_status"] = "REVIEW_REQUIRED"
        chapter["warning"] = (
            "La plage physique est un candidat déduit des pages CONTENTS et des en-têtes constructeur; "
            "elle n'est pas publiée comme vérité avant validation."
        )

    page_contexts: list[dict[str, Any]] = []
    for page_number in sorted(layout_by_page):
        chapter = next(
            (
                item
                for item in chapters
                if item["start_physical_page"] <= page_number <= item["end_physical_page"]
            ),
            None,
        )
        if chapter is None:
            page_contexts.append(
                {
                    "page_number": page_number,
                    "chapter_id": None,
                    "chapter_title_candidate": None,
                    "status": "REVIEW_REQUIRED",
                    "reason": "before_first_resolved_chapter_anchor",
                }
            )
        else:
            page_contexts.append(
                {
                    "page_number": page_number,
                    "chapter_id": chapter["chapter_id"],
                    "chapter_title_candidate": chapter["title_candidate"],
                    "status": "REVIEW_REQUIRED",
                    "reason": "physical_range_from_manufacturer_contents_anchor",
                }
            )

    return {
        "hierarchy_version": HIERARCHY_VERSION,
        "status": "CANDIDATE_ONLY",
        "page_count": len(layout_by_page),
        "contents_anchor_count": len(anchors),
        "chapter_candidate_count": len(chapters),
        "unresolved_chapter_anchor_pages": [
            anchor["physical_page_number"] for anchor in anchors if not anchor["title_candidate"]
        ],
        "anchors": anchors,
        "chapters": chapters,
        "page_contexts": page_contexts,
        "publication_gate": "BLOCKED_REVIEW",
        "policy": (
            "Les chapitres proviennent de la structure du document constructeur. "
            "Aucune liste métier prédéfinie (freins, boîte, moteur, etc.) n'est utilisée pour inventer un chapitre."
        ),
    }


def run_document_hierarchy_audit(
    layout_root: pathlib.Path,
    structure_root: pathlib.Path,
    output_root: pathlib.Path,
) -> dict[str, Any]:
    layout_files = sorted(pathlib.Path(layout_root).glob("*/pages/page-*.json"))
    structure_files = sorted(pathlib.Path(structure_root).glob("*/pages/page-*.json"))
    if not layout_files or not structure_files:
        raise RuntimeError("Layout ou structure absent pour la reconstruction de hiérarchie")
    structure_by_name = {path.name: path for path in structure_files}
    layout_pages = []
    structure_pages = []
    for layout_file in layout_files:
        structure_file = structure_by_name.get(layout_file.name)
        if structure_file is None:
            raise RuntimeError(f"Structure manquante pour {layout_file.name}")
        layout_pages.append(json.loads(layout_file.read_text(encoding="utf-8")))
        structure_pages.append(json.loads(structure_file.read_text(encoding="utf-8")))

    result = build_document_hierarchy(layout_pages, structure_pages)
    output_root = pathlib.Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    pdf_dir_name = layout_files[0].parents[1].name
    pages_out = output_root / pdf_dir_name / "pages"
    pages_out.mkdir(parents=True, exist_ok=True)
    by_page = {int(item["page_number"]): item for item in result["page_contexts"]}
    for page_number, context in by_page.items():
        (pages_out / f"page-{page_number:04d}.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result
