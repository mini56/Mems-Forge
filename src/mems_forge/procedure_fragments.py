from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict
from typing import Any

from mems_forge.structure_candidates import PHASE_LABELS, REPAIR_NO_RE, STEP_RE, _normalise_space

FRAGMENT_VERSION = "0.3.0"


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


_UNIT_VALUE_RE = re.compile(
    r"(?<![\w.])(?P<number>\d+(?:[.,]\d+)?)\s+"
    r"(?P<unit>Nm|N·m|mm|kPa|bar|mV|V|A|Ω|°C|rpm|kg)\b"
)


def _measured_value_candidates(text: str) -> list[dict[str, Any]]:
    """Preserve OCR unit mentions verbatim; neither calibrate nor validate."""
    return [
        {
            "source_text": match.group(),
            "numeric_text": match.group("number"),
            "unit_text": match.group("unit"),
            "verification_state": "OCR_CANDIDATE_UNVERIFIED",
            "approved_for_ai": False,
        }
        for match in _UNIT_VALUE_RE.finditer(text)
    ]


def _is_plausible_step_continuation(
    first: dict[str, Any],
    previous: dict[str, Any],
    following: dict[str, Any],
    text: str,
) -> bool:
    """Require contiguous, indented OCR text in the same original column.

    Drawings, tool identifiers, new procedure headings and notes remain source
    evidence but are never silently appended as a technical instruction.
    """
    if not text or STEP_RE.match(text) or _phase_from_line(text) is not None:
        return False
    if REPAIR_NO_RE.search(text) or re.match(
        r"^(?:WARNING|CAUTION|NOTE)\s*:", text, re.IGNORECASE
    ):
        return False
    if re.fullmatch(r"\d+[A-Z]\d{3,6}", text):
        return False
    letters = [x for x in text if x.isalpha()]
    if len(letters) >= 5 and sum(x.isupper() for x in letters) / len(letters) > 0.9:
        return False
    initial, last, upcoming = (
        item.get("bbox") for item in (first, previous, following)
    )
    if any(not isinstance(box, list) or len(box) != 4 for box in
           (initial, last, upcoming)):
        return False
    if not (initial[0] <= upcoming[0] <= initial[2] and
            upcoming[1] >= last[1] and
            -3 <= upcoming[1] - last[3] <= 24):
        return False
    # Continuation text is typically inset from the numbered step label.
    # A new flush-left line might be a caption or a separate instruction.
    if upcoming[0] < initial[0] + 12:
        return False
    return True


_SOURCE_NOTICE_RE = re.compile(r"^\s*(WARNING|CAUTION|NOTE)\s*:\s*(.*)$", re.IGNORECASE)


def _source_notices(layout: dict[str, Any]) -> list[dict[str, Any]]:
    """Read actual safety/note headings in every body region, even completed repairs.

    Source notices are NOT steps. A notice's relationship to the nearest
    numbered repair is unknown until independently resolved. Preserve its
    original TSV line keys and pixel bboxes for the source-proofed SQLite.
    """
    page_number = int(layout["page_number"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in layout.get("lines", []):
        region = str(line.get("region") or "unknown")
        if region in {"header", "footer", "spanning", "unknown"}:
            continue
        grouped[region].append(line)
    output: list[dict[str, Any]] = []
    for region, lines in grouped.items():
        for pos, line in enumerate(lines):
            source_text = _normalise_space(str(line.get("text", "")))
            match = _SOURCE_NOTICE_RE.match(source_text)
            if not match:
                continue
            kind = match.group(1).upper()
            references = [_line_ref(line)]
            # A long warning may wrap to an indented second line. Collect
            # only geometrically adjacent candidates, leave all other content
            # in the raw OCR. Never consume the beginning of a numbered step.
            for follower in lines[pos + 1:]:
                follower_text = _normalise_space(str(follower.get("text", "")))
                if (not follower_text
                        or _SOURCE_NOTICE_RE.match(follower_text)
                        or not _is_plausible_step_continuation(
                            line, lines[pos + len(references) - 1],
                            follower, follower_text,
                        )):
                    break
                references.append(_line_ref(follower))
            output.append({
                "page_number": page_number,
                "region": region,
                "kind": kind,
                "source_text": " ".join(ref["text"] for ref in references),
                "source_lines": references,
                "source_line_count": len(references),
                "relationship_to_procedure": "UNRESOLVED",
                "validation_state": "OCR_SOURCE_NOTICE_UNVERIFIED",
                "approved_for_ai": False,
            })
    return output


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
        if any(REPAIR_NO_RE.search(_normalise_space(str(line.get("text", ""))))
               for line in lines):
            continue
        if region in complete_regions:
            continue

        numbered: list[dict[str, Any]] = []
        phase_markers: list[dict[str, Any]] = []
        orphan_lines: list[dict[str, Any]] = []
        current_step: dict[str, Any] | None = None
        current_first: dict[str, Any] | None = None
        current_last: dict[str, Any] | None = None
        for line in lines:
            text = _normalise_space(str(line.get("text", "")))
            if not text:
                continue
            phase = _phase_from_line(text)
            if phase is not None:
                phase_markers.append({
                    "label": phase[0],
                    "source_label": phase[1],
                    "source": _line_ref(line),
                })
                current_step = None
                current_first = None
                current_last = None
                continue
            match = STEP_RE.match(text)
            if match:
                first_source = _line_ref(line)
                current_step = {
                    "number": int(match.group(1)),
                    "text": match.group(2).strip(),
                    "source": first_source,
                    "source_lines": [first_source],
                    "source_line_count": 1,
                    "measurement_mentions": [],
                    "validation_state": "OCR_MULTILINE_CANDIDATE_UNVERIFIED",
                    "approved_for_ai": False,
                }
                numbered.append(current_step)
                current_first = line
                current_last = line
                continue
            if (current_step is not None and current_first is not None
                    and current_last is not None
                    and _is_plausible_step_continuation(
                        current_first, current_last, line, text
                    )):
                current_step["text"] = (current_step["text"] + " " + text).strip()
                current_step["source_lines"].append(_line_ref(line))
                current_step["source_line_count"] += 1
                current_last = line
            else:
                orphan_lines.append(_line_ref(line))
                # Do not append an isolated caption to the following numbered
                # step. The original OCR remains available for human review.
                current_step = None
                current_first = None
                current_last = None

        if len(numbered) < 2:
            continue

        numbers = [item["number"] for item in numbered]
        starts_after_one = numbers[0] > 1
        has_phase_marker = bool(phase_markers)
        if not has_phase_marker and not starts_after_one:
            continue

        for item in numbered:
            item["measurement_mentions"] = _measured_value_candidates(item["text"])
        reasons = ["no_local_service_repair_number"]
        if has_phase_marker:
            reasons.append("procedure_phase_heading_present")
        if starts_after_one:
            reasons.append("numbering_starts_after_one")

        results.append({
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
            "unassigned_ocr_lines": orphan_lines,
            "source_completeness": (
                "MULTILINE_OCR_CANDIDATE_UNVERIFIED"
            ),
            "evidence": reasons,
            "warning": (
                "Chaque ligne conservée est une hypothèse de lecture OCR, "
                "jamais une consigne de réparation certifiée."
            ),
        })

    return results


def analyze_page_fragments(layout: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    if int(layout["page_number"]) != int(structure["page_number"]):
        raise ValueError("layout/structure page mismatch")
    fragments = _region_fragments(layout, structure)
    notices = _source_notices(layout)
    return {
        "fragment_version": FRAGMENT_VERSION,
        "page_number": int(layout["page_number"]),
        "fragment_count": len(fragments),
        "fragments": fragments,
        "source_notice_count": len(notices),
        "source_notices": notices,
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


def _numbered_repair_family(repair_number: str | None) -> str | None:
    """Read the leading numbered constructor family, not an inferred chapter."""
    if not isinstance(repair_number, str):
        return None
    head, separator, tail = repair_number.partition(".")
    if not separator or not tail or not head.isdigit():
        return None
    return head


def _adjacency_candidates(
    page_results: list[dict[str, Any]],
    hierarchy: dict[int, dict[str, Any]],
    structures_by_page: dict[int, dict[str, Any]] | None = None,
    physical_page_map: dict[int, int] | None = None,
    source_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Find plausible original-PDF continuations without merging procedures.

    Merely listing fragments on consecutive sample pages is NOT evidence of a
    continuous repair. A candidate link requires actual original physical
    adjacency, a shared manufacturer chapter, an unambiguous previous repair
    identity and either step progression or an explicit Refit heading.
    If the excerpt has no validated chapter heading, a narrower source-backed
    exception permits a REVIEW-ONLY candidate when right-page Remove continues
    as left-page Refit, while the new right-side procedure has a different
    manufacturer repair number in the same numbered family. The actual chapter
    remains unresolved; it is never inferred or used to authorize a merge.
    """
    by_page = {int(page["page_number"]): page for page in page_results}
    structures = structures_by_page or {}
    real_page = physical_page_map or {number: number for number in by_page}
    if set(real_page) != set(by_page):
        raise ValueError("PHYSICAL_PAGE_MAPPING_INCOMPLETE")
    if (any(type(value) is not int or value < 1 for value in real_page.values())
            or len(set(real_page.values())) != len(real_page)
            or [real_page[n] for n in sorted(real_page)] != sorted(real_page.values())):
        raise ValueError("PHYSICAL_PAGE_MAPPING_NOT_STRICTLY_INCREASING")

    hints: list[dict[str, Any]] = []
    inherited: dict[int, list[dict[str, Any]]] = {}
    for page_number in sorted(by_page):
        current = by_page[page_number]
        previous = page_number - 1
        if (not current.get("fragments") or previous not in by_page
                or real_page[previous] + 1 != real_page[page_number]):
            continue
        same_chapter = _same_resolved_chapter(hierarchy, previous, page_number)

        candidates: list[dict[str, Any]] = []
        for proc in structures.get(previous, {}).get("procedures", []):
            repair = proc.get("repair_number")
            steps = proc.get("steps") or []
            if not repair or not steps:
                continue
            last = steps[-1]
            if not isinstance(last.get("number"), int):
                continue
            candidates.append({
                "repair_number": repair,
                "origin_region": proc.get("region"),
                "last_step": last["number"],
                "last_phase": last.get("phase"),
                "origin_page": previous,
                "from_kind": "explicit_manufacturer_repair_number",
            })
        for inherited_link in inherited.get(previous, []):
            candidates.append({
                "repair_number": inherited_link["candidate_repair_number"],
                "origin_region": inherited_link.get("to_region"),
                "last_step": inherited_link["fragment_step_numbers"][-1],
                "last_phase": inherited_link.get("candidate_phase"),
                "origin_page": inherited_link["origin_procedure_page"],
                "from_kind": "previous_candidate_continuation",
            })
        # No identity, or several plausible procedures on the preceding page:
        # never make a speculative or potentially cross-wired linkage.
        if len(candidates) != 1:
            continue
        origin = candidates[0]
        for fragment in current["fragments"]:
            numbered = fragment.get("step_numbers") or []
            if not numbered or not all(type(i) is int for i in numbered):
                continue
            if numbered != list(range(numbered[0], numbered[0] + len(numbered))):
                continue
            first = numbered[0]
            phases = [p.get("label") for p in fragment.get("phase_markers") or []]
            advances = first == origin["last_step"] + 1
            explicit_refit = (
                first == 1 and phases == ["refit"]
                and origin["last_phase"] != "refit"
            )
            if not (advances or explicit_refit):
                continue
            if fragment.get("status") != "REVIEW_REQUIRED":
                continue
            # Never invent a chapter from scattered OCR. The actual manufacturer
            # PDF 92(right Remove 12.65.12) -> 93(left Refit) -> 93(right new
            # 12.65.05) is an unambiguous *candidate* reading transition even
            # if OCR has lost its chapter/section header. It remains unapproved.
            cross_page_fallback = False
            if not same_chapter:
                repair_family = _numbered_repair_family(origin["repair_number"])
                new_right_repairs = [
                    proc.get("repair_number")
                    for proc in structures.get(page_number, {}).get("procedures", [])
                    if proc.get("region") == "right"
                ]
                cross_page_fallback = bool(
                    origin["from_kind"] == "explicit_manufacturer_repair_number"
                    and origin["origin_region"] == "right"
                    and fragment.get("region") == "left"
                    and explicit_refit
                    and origin["last_phase"] in ("remove", "dismantle")
                    and repair_family is not None
                    and len(new_right_repairs) == 1
                    and new_right_repairs[0] != origin["repair_number"]
                    and _numbered_repair_family(new_right_repairs[0])
                    == repair_family
                )
                if not cross_page_fallback:
                    continue
            evidence = [
                "adjacent_original_physical_pages",
                ("same_manufacturer_chapter_candidate" if same_chapter
                 else "chapter_unresolved_review_required"),
                origin["from_kind"],
                "step_numbers_progress" if advances else "explicit_refit_phase_restart",
            ]
            if cross_page_fallback:
                evidence += [
                    "original_right_column_to_next_left_column",
                    "new_right_column_has_distinct_repair_same_numbered_family",
                ]
            hint = {
                "kind": "cross_page_link_candidate",
                "status": "REVIEW_REQUIRED",
                "source_pdf_sha256": source_sha256,
                "from_page": previous,
                "to_page": page_number,
                "from_original_physical_page": real_page[previous],
                "to_original_physical_page": real_page[page_number],
                "origin_procedure_page": origin["origin_page"],
                "candidate_repair_number": origin["repair_number"],
                "candidate_phase": (
                    phases[0] if len(phases) == 1 else origin["last_phase"]
                ),
                "to_region": fragment.get("region"),
                "chapter_id": (
                    hierarchy[page_number]["chapter_id"]
                    if same_chapter else "UNRESOLVED"
                ),
                "fragment_step_numbers": numbered,
                "fragment_source_lines": [
                    line.get("source") for line in fragment.get("numbered_lines", [])
                ],
                "fragment_complete_steps": [
                    {
                        "number": step["number"],
                        "text": step["text"],
                        "source_lines": step.get("source_lines") or [step["source"]],
                        "source_line_count": len(
                            step.get("source_lines") or [step["source"]]
                        ),
                        "measurement_mentions": step.get("measurement_mentions") or [],
                        "validation_state": "OCR_MULTILINE_CANDIDATE_UNVERIFIED",
                        "approved_for_ai": False,
                    }
                    for step in fragment.get("numbered_lines", [])
                ],
                "evidence": evidence,
                "approved_for_ai": False,
                "warning": (
                    "Lien candidat uniquement. Une suite de pages ne suffit pas "
                    "pour valider ou fusionner les instructions techniques."
                ),
            }
            hints.append(hint)
            inherited.setdefault(page_number, []).append(hint)
    return hints


def run_fragment_prototype(
    layout_root: pathlib.Path,
    structure_root: pathlib.Path,
    output_root: pathlib.Path,
    hierarchy_root: pathlib.Path | None = None,
    *,
    source_pdf_sha256: str | None = None,
    original_physical_page_map: dict[int, int] | None = None,
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

    hints = _adjacency_candidates(
        results, hierarchy, structures_by_page,
        original_physical_page_map, source_pdf_sha256,
    )
    raw_count = sum(int(page.get("raw_fragment_count", page.get("fragment_count", 0))) for page in results)
    retained_count = sum(int(page["fragment_count"]) for page in results)
    summary = {
        "fragment_version": FRAGMENT_VERSION,
        "page_count": len(results),
        "raw_fragment_candidate_count": raw_count,
        "continuation_fragment_count": retained_count,
        "rejected_raw_fragment_count": raw_count - retained_count,
        "fragment_pages": [page["page_number"] for page in results if page["fragment_count"]],
        "source_notice_count": sum(page["source_notice_count"] for page in results),
        "source_notice_pages": [
            page["page_number"] for page in results if page["source_notice_count"]
        ],
        "cross_page_link_candidate_count": len(hints),
        "cross_page_link_candidates": hints,
        "source_pdf_sha256": source_pdf_sha256,
        "original_physical_page_map": {
            str(k): v for k, v in sorted(
                (original_physical_page_map or {
                    n: n for n in structures_by_page
                }).items()
            )
        },
        "automatic_procedure_merges": 0,
        "hierarchy_context_used": bool(hierarchy),
        "publication_gate": (
            "BLOCKED_REVIEW"
            if hints or any(
                page["fragment_count"] or page["source_notice_count"]
                for page in results
            )
            else "NO_FRAGMENT_BLOCKER"
        ),
    }
    pathlib.Path(output_root).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(output_root) / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
