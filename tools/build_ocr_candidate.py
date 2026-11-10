"""Create the raw + full OCR candidate from the audited deposited PDF."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from mems_forge.document_pack import build_document_pack
from mems_forge.pdf_reader import sha256_file


def main() -> int:
    root = Path("out/full-audit")
    summary = root / "audit_summary.json"
    if not summary.is_file():
        print("FULL_DOCUMENT_AUDIT_MISSING", file=sys.stderr)
        return 2
    data = json.loads(summary.read_text(encoding="utf-8"))
    sha = data["source_sha256"]
    pdfs = sorted(
        p for p in Path("depot").rglob("*")
        if p.is_file() and p.suffix.lower() == ".pdf" and sha256_file(p) == sha
    )
    if len(pdfs) != 1:
        print(f"FULL_DOCUMENT_PDF_IDENTITY_NOT_UNIQUE:{len(pdfs)}", file=sys.stderr)
        return 2
    out = Path("out/candidate_packs_full_ocr")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"mems-forge-full-ocr-{sha[:24]}.zip"
    result = build_document_pack(
        pdfs[0], target, ocr_root=root / "ocr",
    )
    manifest = result["manifest"]
    if manifest["counts"]["ocr_pages"] != manifest["source"]["pages"]:
        print("FULL_OCR_PAGE_COUNT_INCOMPLETE", file=sys.stderr)
        return 2
    (out / "report.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"FULL_OCR_CANDIDATE_BUILT: {target}")
    print(f"SOURCE_PAGES={manifest['source']['pages']} OCR_PAGES={manifest['counts']['ocr_pages']}")
    print("PUBLICATION_BLOCKED: human and semantic validation required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
