from __future__ import annotations

import json
import pathlib

from mems_forge.structure_candidates import run_structure_prototype


def main() -> int:
    summary = run_structure_prototype(
        pathlib.Path("out/layout-prototype"),
        pathlib.Path("out/structure-prototype"),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
