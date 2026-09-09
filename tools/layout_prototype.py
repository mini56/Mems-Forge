from __future__ import annotations

import json
import pathlib

from mems_forge.ocr_layout import run_layout_prototype


def main() -> int:
    summary = run_layout_prototype(
        pathlib.Path("out/ocr-prototype"),
        pathlib.Path("out/layout-prototype"),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
