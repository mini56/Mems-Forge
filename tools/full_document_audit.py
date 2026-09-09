from __future__ import annotations

import json
import pathlib

from mems_forge.full_document_audit import run_full_document_audit


def main() -> int:
    depot = pathlib.Path("depot")
    pdfs = sorted(
        path
        for path in depot.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )
    if not pdfs:
        print("Aucun PDF trouvé dans depot/.")
        return 0

    # The Windows deposit tool creates timestamped folders, so lexical ordering
    # identifies the latest deposited test source without mutating older sources.
    pdf = pdfs[-1]
    print(f"Full document audit: {pdf.as_posix()}")
    summary = run_full_document_audit(pdf, pathlib.Path("out/full-audit"))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
