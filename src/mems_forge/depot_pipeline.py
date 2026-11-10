"""Per-document full OCR processing for deposited PDFs.

One source SHA -> one isolated audit directory -> one candidate ZIP.
Never publishes, installs, or upgrades the MEMS Manager application.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable

from .audit_postprocess import postprocess_full_audit
from .document_hierarchy import run_document_hierarchy_audit
from .document_pack import build_document_pack, inspect_document_pack
from .full_document_audit import run_full_document_audit
from .ocr_quality import run_ocr_quality_audit
from .ocr_review import refine_full_ocr_review
from .procedure_fragments import run_fragment_prototype

_GIT_SHA = re.compile(r"^[a-fA-F0-9]{40,64}$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def select_depot_pdfs(
    repository_root: Path, *, event: str, before_sha: str = "", head: str = "HEAD"
) -> list[Path]:
    """On push, select *all changed* PDFs; manual runs may audit the full depot.

    A missing Git history is an error, not an excuse to silently audit a
    different source. A code-only push does not re-run hours of old OCR.
    """
    repository_root = Path(repository_root)
    depot = repository_root / "depot"
    all_pdfs = sorted(
        p for p in depot.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"
    ) if depot.exists() else []
    if event != "push" or not before_sha or set(before_sha) == {"0"}:
        return all_pdfs
    if not _GIT_SHA.fullmatch(before_sha):
        raise ValueError("Invalid Git base SHA for PDF selection")
    if head != "HEAD" and not _GIT_SHA.fullmatch(head):
        raise ValueError("Invalid Git head SHA for PDF selection")
    diff = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AMR",
         before_sha, head, "--", "depot"],
        cwd=repository_root, check=True, capture_output=True, text=True,
    )
    selected = []
    for line in diff.stdout.splitlines():
        relative = Path(line.strip())
        if not relative.parts or relative.parts[0] != "depot":
            continue
        path = repository_root / relative
        if path.is_file() and path.suffix.lower() == ".pdf":
            selected.append(path)
    return sorted(set(selected))


def process_one_document(pdf: Path, output_root: Path) -> dict:
    """Run each extraction layer in its original source module, isolated by SHA."""
    pdf = Path(pdf)
    sha = sha256_file(pdf)
    audit_root = Path(output_root) / "full-audit" / sha
    pack_root = Path(output_root) / "candidate_packs_full_ocr"
    target = pack_root / f"mems-forge-full-ocr-{sha[:24]}.zip"
    if target.exists():
        verdict = inspect_document_pack(target)
        if verdict.get("valid") and sha == _source_sha_from_pack(target):
            return {"status": "ALREADY_PRESENT", "source_sha256": sha,
                    "pack": str(target), "pages": verdict["source_pages"]}
        raise ValueError("An existing full-OCR candidate is invalid or belongs to another PDF")

    run_full_document_audit(pdf, audit_root)
    refine_full_ocr_review(audit_root)
    run_document_hierarchy_audit(
        audit_root / "layout", audit_root / "structure", audit_root / "hierarchy"
    )
    run_ocr_quality_audit(audit_root / "ocr", audit_root / "ocr-quality")
    run_fragment_prototype(
        audit_root / "layout", audit_root / "structure",
        audit_root / "fragments", audit_root / "hierarchy",
    )
    postprocess_full_audit(audit_root)
    pack_root.mkdir(parents=True, exist_ok=True)
    built = build_document_pack(pdf, target, ocr_root=audit_root / "ocr")
    # Source parity is compulsory for any candidate produced by the depot.
    # It does not grant semantic validation or authorize publication.
    from .source_validation import write_source_audit
    source_audit = write_source_audit(
        target, audit_root / "source_validation.json"
    )
    if not source_audit["native_source_parity_ok"]:
        target.unlink(missing_ok=True)
        raise ValueError("SOURCE_PDF_TO_SQLITE_PARITY_FAILED: " +
                         ",".join(issue["code"] for issue in source_audit["issues"]))

    manifest = built["manifest"]
    if manifest["counts"]["ocr_pages"] != manifest["source"]["pages"]:
        target.unlink(missing_ok=True)
        raise ValueError("FULL_OCR_PAGE_COUNT_INCOMPLETE")
    return {
        "status": "CANDIDATE_NOT_VALIDATED",
        "source_sha256": sha,
        "source_filename": pdf.name,
        "pack": str(target),
        "pages": manifest["source"]["pages"],
        "ocr_pages": manifest["counts"]["ocr_pages"],
        "pack_sha256": sha256_file(target),
        "source_parity_verified": True,
        "source_review_items": len(source_audit["reviews"]),
        "source_validation_report": str(audit_root / "source_validation.json"),
        "approved_for_ai": False,
    }


def _source_sha_from_pack(path: Path) -> str:
    import zipfile
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("manifest.json"))["source"]["sha256"]


def audit_depot(
    pdfs: list[Path], output_root: Path,
    *, processor: Callable[[Path, Path], dict] = process_one_document,
) -> dict:
    """Do not let one damaged PDF hide which other sources were successfully audited."""
    output_root = Path(output_root)
    successes = []
    blockers = []
    duplicates = []
    seen = set()
    for pdf in pdfs:
        pdf = Path(pdf)
        try:
            sha = sha256_file(pdf)
            if sha in seen:
                duplicates.append({"pdf": str(pdf), "source_sha256": sha})
                continue
            seen.add(sha)
            result = processor(pdf, output_root)
            successes.append({"pdf": str(pdf), **result})
        except Exception as exc:
            blockers.append({"pdf": str(pdf), "error": f"{type(exc).__name__}: {exc}"})
    report = {
        "mode": "INDEPENDENT_DOCUMENT_CANDIDATES",
        "publication_state": "NOT_PUBLISHED",
        "selected_count": len(pdfs),
        "processed_count": len(successes),
        "blocked_count": len(blockers),
        "duplicate_source_count": len(duplicates),
        "processed": successes,
        "blocked": blockers,
        "duplicates": duplicates,
    }
    summary = output_root / "full-audit" / "depot_summary.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report
