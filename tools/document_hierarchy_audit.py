from __future__ import annotations

import json
import pathlib

from mems_forge.document_hierarchy import run_document_hierarchy_audit


def main() -> int:
    summary = run_document_hierarchy_audit(
        pathlib.Path("out/full-audit/layout"),
        pathlib.Path("out/full-audit/structure"),
        pathlib.Path("out/full-audit/hierarchy"),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
