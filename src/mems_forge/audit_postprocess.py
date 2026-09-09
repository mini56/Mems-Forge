from __future__ import annotations

import json
import pathlib
from typing import Any

POSTPROCESS_VERSION = "0.1.0"


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def postprocess_full_audit(root: pathlib.Path) -> dict[str, Any]:
    root = pathlib.Path(root)
    summary_path = root / "audit_summary.json"
    if not summary_path.exists():
        raise RuntimeError(f"Résumé full audit absent: {summary_path}")

    summary = _load_json(summary_path)
    structure_files = sorted((root / "structure").glob("*/pages/page-*.json"))
    if not structure_files:
        raise RuntimeError("Aucune structure de page dans le full audit")

    procedures: list[dict[str, Any]] = []
    for page_file in structure_files:
        page = _load_json(page_file)
        procedures.extend(page.get("procedures", []))

    sequence_issues: list[dict[str, Any]] = []
    for procedure in procedures:
        status = procedure.get("step_sequence_status")
        if status == "contiguous_by_phase":
            continue
        sequence_issues.append(
            {
                "page_number": procedure.get("page_number"),
                "repair_number": procedure.get("repair_number"),
                "title": procedure.get("title"),
                "status": status,
                "step_numbers": procedure.get("step_numbers", []),
                "phase_sequence_statuses": procedure.get("phase_sequence_statuses", []),
                "review_reasons": procedure.get("review_reasons", []),
            }
        )

    structure_summary = summary.setdefault("structure", {})
    structure_summary["procedure_candidate_count"] = len(procedures)
    structure_summary["procedure_step_sequence_issue_count"] = len(sequence_issues)
    structure_summary["procedure_step_sequence_issues"] = sequence_issues
    structure_summary["sequence_validation_rule"] = "manufacturer_phase_local"

    fragments_path = root / "fragments" / "summary.json"
    if fragments_path.exists():
        fragments = _load_json(fragments_path)
        summary["continuation_fragments"] = {
            "fragment_version": fragments.get("fragment_version"),
            "continuation_fragment_count": fragments.get("continuation_fragment_count", 0),
            "fragment_pages": fragments.get("fragment_pages", []),
            "cross_page_link_candidate_count": fragments.get("cross_page_link_candidate_count", 0),
            "cross_page_link_candidates": fragments.get("cross_page_link_candidates", []),
            "link_policy": "review_only_no_automatic_merge",
        }

    blocking = [
        reason
        for reason in summary.get("blocking_reasons", [])
        if reason not in {
            "procedure_step_sequence_issues_require_review",
            "continuation_fragments_require_review",
        }
    ]
    if sequence_issues:
        blocking.append("procedure_step_sequence_issues_require_review")
    fragment_count = summary.get("continuation_fragments", {}).get("continuation_fragment_count", 0)
    if fragment_count:
        blocking.append("continuation_fragments_require_review")
    if "semantic_and_human_validation_not_completed" not in blocking:
        blocking.append("semantic_and_human_validation_not_completed")

    summary["blocking_reasons"] = blocking
    summary["publication_gate"] = "BLOCKED_REVIEW"
    summary["postprocess_version"] = POSTPROCESS_VERSION
    summary["sequence_correction_note"] = (
        "La continuité des étapes est contrôlée par phase constructeur. "
        "Un redémarrage à 1 entre Remove et Refit n'est pas une erreur."
    )
    _write_json(summary_path, summary)
    return summary
