from __future__ import annotations

import json
import pathlib
import sys

from mems_forge.ocr_reader import run_ocr_prototype


def main() -> int:
    depot = pathlib.Path("depot")
    pdfs = sorted(
        p for p in depot.rglob("*")
        if p.is_file() and p.suffix.lower() == ".pdf"
    )
    if not pdfs:
        print("Aucun PDF trouvé dans depot/.")
        return 0

    # Prototype volontairement limité au dernier PDF déposé.
    # Le but est de valider la qualité OCR avant de lancer 482 pages.
    pdf = pdfs[-1]
    print(f"OCR prototype: {pdf.as_posix()}")
    summary = run_ocr_prototype(pdf, pathlib.Path("out/ocr-prototype"))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
