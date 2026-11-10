"""Full-document MEMS Forge audit for every selected PDF; no Windows Setup."""
from __future__ import annotations

import json
import os
from pathlib import Path

from mems_forge.depot_pipeline import audit_depot, select_depot_pdfs


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    before = os.environ.get("MEMS_FORGE_BEFORE_SHA", "")
    try:
        pdfs = select_depot_pdfs(Path.cwd(), event=event, before_sha=before)
        report = audit_depot(pdfs, Path("out"))
    except Exception as exc:
        print(f"DEPOSITOIRE_PDF_INVALIDE: {type(exc).__name__}: {exc}")
        return 2
    print(json.dumps({
        "selected": report["selected_count"],
        "audited": report["processed_count"],
        "blocked": report["blocked_count"],
        "duplicate_sources": report["duplicate_source_count"],
        "publication": report["publication_state"],
        "report": "out/full-audit/depot_summary.json",
    }, ensure_ascii=False, indent=2))
    return 2 if report["blocked_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
