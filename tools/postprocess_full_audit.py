from __future__ import annotations

import json
import pathlib

from mems_forge.audit_postprocess import postprocess_full_audit


def main() -> int:
    summary = postprocess_full_audit(pathlib.Path("out/full-audit"))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
