from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

from mems_forge.pdf_reader import analyze_pdf


def _all_pdfs() -> list[pathlib.Path]:
    depot = pathlib.Path("depot")
    return sorted(
        p for p in depot.rglob("*")
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


def _changed_pdfs() -> list[pathlib.Path]:
    before = os.environ.get("MEMS_FORGE_BEFORE_SHA", "").strip()
    current = os.environ.get("GITHUB_SHA", "HEAD").strip() or "HEAD"
    if not before or set(before) == {"0"}:
        return []
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", before, current, "--", "depot"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    selected = []
    for raw in result.stdout.splitlines():
        path = pathlib.Path(raw.strip())
        if path.suffix.lower() == ".pdf" and path.is_file():
            selected.append(path)
    return sorted(set(selected))


def main() -> int:
    changed = _changed_pdfs()
    pdfs = changed if changed else _all_pdfs()
    mode = "changed_pdf_only" if changed else "all_pdfs_regression"

    if not pdfs:
        print("Aucun PDF à traiter dans depot/.")
        return 0

    print(f"Mode de traitement: {mode}")
    print(f"PDF sélectionnés: {len(pdfs)}")

    summaries = []
    failures = []
    output_root = pathlib.Path("out/reader")
    output_root.mkdir(parents=True, exist_ok=True)

    for pdf in pdfs:
        print(f"\n=== Lecture MEMS Forge: {pdf.as_posix()} ===")
        try:
            summary = analyze_pdf(pdf, output_root)
            summaries.append(summary)
            counts = summary["counts"]
            gate = summary["publication_gate"]
            print(f"pages={counts['pages']}")
            print(f"native_text_pages={counts['native_text_pages']}")
            print(f"raster_without_native_text_pages={counts['raster_without_native_text_pages']}")
            print(f"vector_without_native_text_pages={counts['vector_without_native_text_pages']}")
            print(f"blank_verified_pages={counts['blank_verified_pages']}")
            print(f"possible_multicolumn_pages={counts['possible_multicolumn_pages']}")
            print(f"raw_blocks={counts['raw_blocks']}")
            print(f"raw_spans={counts['raw_spans']}")
            print(f"image_occurrences={counts['image_occurrences']}")
            print(f"vector_drawings={counts['vector_drawings']}")
            print(f"blocking_review_items={counts['blocking_review_items']}")
            print(f"publication_gate={gate['status']}")
        except Exception as exc:
            failures.append({"path": pdf.as_posix(), "error": f"{type(exc).__name__}: {exc}"})
            print(f"ERREUR: {type(exc).__name__}: {exc}", file=sys.stderr)

    aggregate = {
        "mode": mode,
        "selected_pdf_count": len(pdfs),
        "processed_pdf_count": len(summaries),
        "failed_pdf_count": len(failures),
        "summaries": summaries,
        "failures": failures,
    }
    aggregate_path = output_root / "run_summary.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nRésumé global: {aggregate_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
