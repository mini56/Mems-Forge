from __future__ import annotations

import json
import pathlib

from mems_forge.ocr_quality import run_ocr_quality_audit


def main() -> None:
    ocr_root = pathlib.Path("out/full-audit/ocr")
    output_root = pathlib.Path("out/full-audit/ocr-quality")
    result = run_ocr_quality_audit(ocr_root, output_root)
    print(json.dumps({
        "ocr_quality_version": result["ocr_quality_version"],
        "lexicon_size": result["lexicon_size"],
        "spelling_candidate_count": result["spelling_candidate_count"],
        "pages_blocked_for_translation": len(result["pages_blocked_for_translation"]),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
