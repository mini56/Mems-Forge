"""Turn completed PDF reader output into independently verifiable candidate packs.

Executed automatically after the depot/ GitHub workflow reads deposited PDF.
Candidates are evidence, NOT published technical knowledge packs.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from mems_forge.document_pack import build_document_pack

ROOT = Path("out/reader")
OUTPUT = Path("out/candidate_packs")


def main() -> int:
    summary_file = ROOT / "run_summary.json"
    if not summary_file.is_file():
        print("MEMS_FORGE_READER_SUMMARY_ABSENT", file=sys.stderr)
        return 2
    data = json.loads(summary_file.read_text(encoding="utf-8"))
    if data.get("failed_pdf_count", 0) or not data.get("summaries"):
        print("MEMS_FORGE_READER_FAILED_OR_NO_PDFS", file=sys.stderr)
        return 2
    report = {"created": [], "errors": []}
    for item in data["summaries"]:
        source = item["source"]
        pdf = Path(source["source_path"])
        output = OUTPUT / f"mems-forge-{source['sha256'][:24]}.zip"
        try:
            result = build_document_pack(pdf, output, reader_root=ROOT)
            report["created"].append({
                "pdf": str(pdf),
                "pack": str(output),
                "source_sha256": source["sha256"],
                "pages": result["manifest"]["source"]["pages"],
                "publication_state": result["manifest"]["publication_state"],
            })
            print(f"MEMS_FORGE_CANDIDATE_OK: {pdf} -> {output}")
        except (OSError, ValueError, KeyError) as exc:
            report["errors"].append({"pdf": str(pdf), "error": str(exc)})
            print(f"MEMS_FORGE_CANDIDATE_FAILED: {pdf}: {exc}", file=sys.stderr)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 2 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
