from __future__ import annotations

import json
import pathlib

from mems_forge.procedure_fragments import run_fragment_prototype


def main() -> int:
    summary = run_fragment_prototype(
        pathlib.Path("out/layout-prototype"),
        pathlib.Path("out/structure-prototype"),
        pathlib.Path("out/fragment-prototype"),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
