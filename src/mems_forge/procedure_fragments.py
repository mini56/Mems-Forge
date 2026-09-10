from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from typing import Any

from mems_forge.structure_candidates import PHASE_LABELS, REPAIR_NO_RE, STEP_RE, _normalise_space

FRAGMENT_VERSION = "0.2.0"


def _phase_from_line(text: str) -> tuple[str, str] | None:
    source = _normalise_space(text).strip(" :.-").lower()
    canonical = PHASE_LABELS.get(source)
    if canonical is None:
        return None
    return canonical, source


def _line_ref(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "region": line.get("region"),
        "source_line_key": line.get("source_line_key"),
        "bbox": line.get("bbox"),
        "mean_confidence": line.get("mean_confidence"),
        "text": line.get("text", ""),
    }


def _region_fragments(layout: dict[str, Any], structure: dict[str, Any]) -> list[dict[str, Any]]:
    page_number = int(layout["page_number"])
    complete_regions = {str(proc.get("region")) for proc in structure.get("procedures", [])}

    by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in layout.get("lines", []):
        region = str(line.get("region") or "unknown")
        if region in {"header", "footer", "spanning"}:
            continue
        by_region[region].append(line)

    results: list[dict[str, Any]] = []
    for region, lines in by_region.items():
        if any(REPAIR_NO_RE.search(_normalise_space(str(line.get("text", "")))) for line in lines):
            continue
        if region in complete_regions:
            continue

        numbered: list[dict[str, Any]] = []
        phase_markers: list[dict[str, Any]] = []
        for line in lines:
            text = _normalise_space(str(line.get("text", "")))
            phase = _phase_from_line(text)
            if phase is not None:
                phase_markers.append(
                    {
                        "label": phase[0],
                        "source_label": phase[1],
                        "source": _line_ref(line),
                    }
                )
            match = STEP_RE.match(text)
            if match:
                numbered.append(
                    {
                        "number": int(match.group(1)),
                        "text": match.group(2).strip(),
                        "source": _line_ref(line),
                    }
                )

        if len(numbered) < 2:
            continue

        numbers = [item["number"] for item in numbered]
        starts_after_one = numbers[0] > 1
        has_phase_marker = bool(phase_markers)
        if not has_phase_marker and not starts_after_one:
            continue

        reasons = ["no_local_service_repair_number"]
        if has_phase_marker:
            reasons.append("procedure_phase_heading_present")
        if starts_after_one:
            reasons.append("numbering_starts_after_one")

        results.append(
            {
                "fragment_version": FRAGMENT_VERSION,
                "kind": "continuation_fragment_candidate",
                "status": "REVIEW_REQUIRED",
                "page_number": page_number,
                "region": region,
                "step_numbers": numbers,
                "first_step_number": numbers[0],
                "last_step_number": numbers[-1],
                "phase_markers": phase_markers,
                "numbered_lines": numbered,
                "evidence": reasons,
                "warning": (
                    "Fragment sans identité locale. Il ne doit jamais être fusionné "
                    "automatiquement avec une procédure de la page précédente."
                ),
            }
        )

    return results


def analyze_page_fragments(layout: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    if int(layout["page_number"]) != int(structure["page_number"]):
        raise ValueError("layout/structure page mismatch")
    fragments = _region_fragments(layout, structure)
    return {
        "fragment_version": FRAGMENT_VERSION,
        "page_number": int(layout["page_number"]),
        "fragment_count": len(fragments),
        "fragments": fragments,
    }


def _last_procedure_steps(structure: dict[str, Any] | None) -> list[int]:
    if not structure:
        return []
    result: list[int] = []
    for procedure in structure.get("procedures", []):
        numbers = [int(value) for value in procedure.get("step_numbers", [])]
        if numbers:
            result.append(numbers[-1])
    return result


def _load_hierarchy_contexts(hierarchy_root: pathlib.Path | None) -> dict[int, dict[str, Any]]:
    if hierarchy_root is None:
        return {}
    summary_file = pathlib.Path(hierarchy_root) / "summary.json"
    if not summary_file.exists():
        return {}
    payload = json.loads(summary_file.read_text(encoding="utf-8"))
    return {int(item["page_number"]): item for item in payload.get("page_contexts", [])}


def _same_resolved_chapter(
    hierarchy: dict[int, dict[str, Any]],
    left_page: int,
    right_page: int,
) -> bool:
    left = hierarchy.get(left_page, {})
    right = hierarchy.get(right_page, {})
    left_id = left.get("chapter_id")
    right_id = right.get("chapter_id")
    return bool(left_id and right_id and left_id == right_id)


def _apply_document_context(
    raw_results: list[dict[str, Any]],
    structures_by_page: dict[int, dict[str, Any]],
    hierarchy: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject numbering-only false positives unless adjacent source context supports them.

    A manufacturer phase heading remains strong local evidence. A fragment based
    only on numbering must continue the previous physical page inside the same
    reconstructed manufacturer chapter and must progress from an actual procedure
    or an already accepted fragment. Nothing is auto-linked or published.
    """
    by_page = {int(item["page_number"]): item for item in raw_results}
    accepted_by_page: dict[int, list[dict[str, Any]]] = {}
    filtered_pages: list[dict[str, Any]] = []

    for page_number in sorted(by_page):
        raw_page = by_page[page_number]
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        previous_accepted = accepted_by_page.get(page_number - 1, [])
        same_chapter = page_number > 1 and _same_resolved_chapter(hierarchy, page_number - 1, page_number)
        previous_last_steps = _last_procedure_steps(structures_by_page.get(page_number - 1))

        for fragment in raw_page.get("fragments", []):
            item = dict(fragment)
            context_evidence: list[str] = []

            if fragment.get("phase_markers"):
                context_evidence.append("manufacturer_phase_heading")
                if same_chapter:
                    context_evidence.append("same_manufacturer_chapter")
            else:
                first = int(fragment["first_step_number"])
                if not same_chapter:
                    rejected.append(
                        {
                            "fragment": fragment,
                            "reason": "numbering_only_without_same_resolved_chapter",
                        }
                    )
                    continue
                context_evidence.append("same_manufacturer_chapter")
                procedure_progression = any(last + 1 == first for last in previous_last_steps)
                fragment_progression = any(
                    int(previous["last_step_number"]) + 1 == first for previous in previous_accepted
                )
                if procedure_progression:
                    context_evidence.append("continues_previous_page_procedure_numbering")
                elif fragment_progression:
                    context_evidence.append("continues_previous_page_fragment_numbering")
                else:
                    rejected.append(
                        {
                            "fragment": fragment,
                            "reason": "numbering_only_without_adjacent_procedure_progression",
                        }
                    )
                    continue

            context = hierarchy.get(page_number, {})
            item["chapter_id"] = context.get("chapter_id")
            item["chapter_title_candidate"] = context.get("chapter_title_candidate")
            item["context_evidence"] = context_evidence
            accepted.append(item)

        accepted_by_page[page_number] = accepted
        filtered_pages.append(
            {
                "fragment_version": FRAGMENT_VERSION,
                "page_number": page_number,
                "raw_fragment_count": int(raw_page.get("fragment_count", 0)),
                "fragment_count": len(accepted),
                "fragments": accepted,
                "rejected_raw_fragment_count": len(rejected),
                "rejected_raw_fragments": rejected,
            }
        )
    return filtered_pages


def _adjacency_candidates(
    page_results: list[dict[str, Any]],
    hierarchy: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_page = {int(page["page_number"]): page for page in page_results}
    hints: list[dict[str, Any]] = []
    for page_number in sorted(by_page):
        current = by_page[page_number]
        if not current.get("fragments") or page_number <= 1:
            continue
        if hierarchy and not _same_resolved_chapter(hierarchy, page_number - 1, page_number):
            continue
        for fragment in current["fragments"]:
            evidence = ["adjacent_physical_page"]
            if hierarchy:
                evidence.append("same_manufacturer_chapter")
            evidence.extend(fragment.get("context_evidence", []))
            hints.append(
                {
                    "kind": "cross_page_link_candidate",
                    "status": "REVIEW_REQUIRED",
                    "from_page": page_number - 1,
                    "to_page": page_number,
                    "to_region": fragment["region"],
                    "chapter_id": fragment.get("chapter_id"),
                    "chapter_title_candidate": fragment.get("chapter_title_candidate"),
                    "fragment_step_numbers": fragment["step_numbers"],
                    "evidence": list(dict.fromkeys(evidence)),
                    "resolved_repair_number": None,
                    "warning": "Aucun lien de procédure n'est validé automatiquement.",
                }
            )
    return hints


def run_fragment_prototype(
    layout_root: pathlib.Path,
    structure_root: pathlib.Path,
    output_root: pathlib.Path,
    hierarchy_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    layout_files = sorted(pathlib.Path(layout_root).glob("*/pages/page-*.json"))
    structure_files = sorted(pathlib.Path(structure_root).glob("*/pages/page-*.json"))
    if not layout_files or not structure_files:
        raise RuntimeError("Layout ou structure absent pour l'analyse des fragments")

    structure_by_name = {path.name: path for path in structure_files}
    structures_by_page: dict[int, dict[str, Any]] = {}
    raw_results: list[dict[str, Any]] = []
    pdf_dir_name = layout_files[0].parents[1].name

    for layout_file in layout_files:
        structure_file = structure_by_name.get(layout_file.name)
        if structure_file is None:
            raise RuntimeError(f"Structure manquante pour {layout_file.name}")
        layout = json.loads(layout_file.read_text(encoding="utf-8"))
        structure = json.loads(structure_file.read_text(encoding="utf-8"))
        structures_by_page[int(structure["page_number"])] = structure
        raw_results.append(analyze_page_fragments(layout, structure))

    hierarchy = _load_hierarchy_contexts(hierarchy_root)
    if hierarchy:
        results = _apply_document_context(raw_results, structures_by_page, hierarchy)
    else:
        results = [
            {
                **page,
                "raw_fragment_count": page["fragment_count"],
                "rejected_raw_fragment_count": 0,
                "rejected_raw_fragments": [],
            }
            for page in raw_results
        ]

    out_dir = pathlib.Path(output_root) / pdf_dir_name / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        (out_dir / f"page-{int(result['page_number']):04d}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    hints = _adjacency_candidates(results, hierarchy)
    raw_count = sum(int(page.get("raw_fragment_count", page.get("fragment_count", 0))) for page in results)
    retained_count = sum(int(page["fragment_count"]) for page in results)
    summary = {
        "fragment_version": FRAGMENT_VERSION,
        "page_count": len(results),
        "raw_fragment_candidate_count": raw_count,
        "continuation_fragment_count": retained_count,
        "rejected_raw_fragment_count": raw_count - retained_count,
        "fragment_pages": [page["page_number"] for page in results if page["fragment_count"]],
        "cross_page_link_candidate_count": len(hints),
        "cross_page_link_candidates": hints,
        "hierarchy_context_used": bool(hierarchy),
        "publication_gate": (
            "BLOCKED_REVIEW"
            if hints or any(page["fragment_count"] for page in results)
            else "NO_FRAGMENT_BLOCKER"
        ),
    }
    pathlib.Path(output_root).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(output_root) / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
