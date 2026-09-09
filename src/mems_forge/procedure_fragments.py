from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict
from typing import Any

from mems_forge.structure_candidates import PHASE_LABELS, REPAIR_NO_RE, STEP_RE, _normalise_space

FRAGMENT_VERSION = "0.1.0"


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
        # A region containing a Service Repair number has its own local identity;
        # it is handled by the normal procedure parser and must not become a
        # continuation fragment as well.
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

        # Conservative evidence only. A numbered component list beginning at 1
        # without a procedure phase is not a continuation candidate. Two cases
        # are admitted: a manufacturer phase heading without local repair number,
        # or numbering that visibly continues after step 1.
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


def _adjacency_candidates(page_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create review-only adjacency hints; never auto-link fragments to procedures.

    Evidence is intentionally weak and explicit: adjacent physical pages plus
    plausible step progression. The target repair identity is left unresolved.
    """
    by_page = {int(page["page_number"]): page for page in page_results}
    hints: list[dict[str, Any]] = []
    for page_number in sorted(by_page):
        current = by_page[page_number]
        if not current.get("fragments") or page_number <= 1:
            continue
        previous = by_page.get(page_number - 1)
        if previous is None:
            continue
        for fragment in current["fragments"]:
            evidence = ["adjacent_physical_page"]
            first = int(fragment["first_step_number"])
            if first > 1:
                evidence.append("fragment_starts_after_step_one")
            hints.append(
                {
                    "kind": "cross_page_link_candidate",
                    "status": "REVIEW_REQUIRED",
                    "from_page": page_number - 1,
                    "to_page": page_number,
                    "to_region": fragment["region"],
                    "fragment_step_numbers": fragment["step_numbers"],
                    "evidence": evidence,
                    "resolved_repair_number": None,
                    "warning": "Aucun lien de procédure n'est validé automatiquement.",
                }
            )
    return hints


def run_fragment_prototype(
    layout_root: pathlib.Path,
    structure_root: pathlib.Path,
    output_root: pathlib.Path,
) -> dict[str, Any]:
    layout_files = sorted(pathlib.Path(layout_root).glob("*/pages/page-*.json"))
    structure_files = sorted(pathlib.Path(structure_root).glob("*/pages/page-*.json"))
    if not layout_files or not structure_files:
        raise RuntimeError("Layout ou structure absent pour l'analyse des fragments")

    structure_by_name = {path.name: path for path in structure_files}
    results: list[dict[str, Any]] = []
    for layout_file in layout_files:
        structure_file = structure_by_name.get(layout_file.name)
        if structure_file is None:
            raise RuntimeError(f"Structure manquante pour {layout_file.name}")
        layout = json.loads(layout_file.read_text(encoding="utf-8"))
        structure = json.loads(structure_file.read_text(encoding="utf-8"))
        result = analyze_page_fragments(layout, structure)
        results.append(result)

        pdf_dir_name = layout_file.parents[1].name
        out_dir = pathlib.Path(output_root) / pdf_dir_name / "pages"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / layout_file.name).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    hints = _adjacency_candidates(results)
    summary = {
        "fragment_version": FRAGMENT_VERSION,
        "page_count": len(results),
        "continuation_fragment_count": sum(page["fragment_count"] for page in results),
        "fragment_pages": [page["page_number"] for page in results if page["fragment_count"]],
        "cross_page_link_candidate_count": len(hints),
        "cross_page_link_candidates": hints,
        "publication_gate": "BLOCKED_REVIEW" if hints or any(page["fragment_count"] for page in results) else "NO_FRAGMENT_BLOCKER",
    }
    pathlib.Path(output_root).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(output_root) / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
