from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .database import initialize_database, validate_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mems-forge")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init-db", help="Create an empty MEMS Forge database")
    init_cmd.add_argument("path", type=Path)

    check_cmd = sub.add_parser("check-db", help="Validate a MEMS Forge database")
    check_cmd.add_argument("path", type=Path)

    audit = sub.add_parser(
        "audit-setup",
        help="Compare original RAVE PDFs, their index, image files and the actual installed Setup",
    )
    audit.add_argument("--source-repo", type=Path, required=True,
                       help="Checkout of mini56/ECU-Mems-Manager-Session containing rave/")
    audit.add_argument("--install-root", type=Path, required=True,
                       help="Directory in which the Windows Setup was actually installed")
    audit.add_argument("--baseline", type=Path,
                       default=Path(__file__).resolve().parents[2] /
                       "data" / "rave_47_originals.json")
    audit.add_argument("--report-dir", type=Path, default=Path("out/package-audit"))
    audit.add_argument("--other-pdfs", type=Path,
                       help="Additional user-supplied PDF directory to inventory and test")

    tester = sub.add_parser(
        "tester",
        help="Search technical SQLite bases directly, without a language model",
    )
    tester.add_argument("--db", type=Path,
                        help="Single SQLite database; omit to search both installed databases")
    tester.add_argument("--query", required=True)
    tester.add_argument("--limit", type=int, default=20)
    tester.add_argument("--install-root", type=Path)

    corrected = sub.add_parser(
        "build-corrected-expert",
        help="Rebuild a separate expert SQLite with verified 1750 citations (never overwrite source)",
    )
    corrected.add_argument("--install-root", type=Path, required=True)
    corrected.add_argument("--output", type=Path, required=True)
    corrected.add_argument("--report", type=Path)
    build = sub.add_parser(
        "build-pack", help="Build a verified raw PDF candidate; never auto-publish"
    )
    build.add_argument("--pdf", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--reader-root", type=Path)
    verify = sub.add_parser("verify-pack", help="Verify candidate ZIP and SQLite")
    verify.add_argument("path", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "build-pack":
        from .document_pack import build_document_pack
        result = build_document_pack(
            args.pdf, args.output, reader_root=args.reader_root
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify-pack":
        from .document_pack import inspect_document_pack
        report = inspect_document_pack(args.path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["valid"] else 2

    if args.command == "init-db":
        path = initialize_database(args.path, __version__)
        print(path)
        return 0

    if args.command == "check-db":
        result = validate_database(args.path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["integrity_ok"] and result["foreign_key_errors"] == 0 else 2

    if args.command == "audit-setup":
        from .package_audit import audit_install, write_report

        report = audit_install(
            args.source_repo, args.install_root, args.baseline,
            extra_pdf_root=args.other_pdfs,
        )
        write_report(report, args.report_dir)
        gate = report["publication_gate"]
        print(json.dumps({
            "state": gate["state"], "blockers": gate["blockers"],
            "source_pdfs": report["source_documents"]["ok"],
            "image_count": report["reference_images_and_enrichments"]["available_images"],
            "mapped_to_pack": len(report["document_mapping"]["matched"]),
            "report_dir": str(args.report_dir),
        }, indent=2, ensure_ascii=False))
        return 2 if gate["blockers"] else 0

    if args.command == "build-corrected-expert":
        from .source_corrections import build_corrected_expert_database
        result = build_corrected_expert_database(
            args.install_root, args.output, evidence_report=args.report
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "tester":
        from .db_tester import search_database, search_installed_databases

        if args.db is None and args.install_root is None:
            parser = build_parser()
            parser.error("tester: specify --db or --install-root")
        if args.db is None:
            result = search_installed_databases(
                args.install_root, args.query, limit=args.limit
            )
        else:
            result = search_database(
                args.db, args.query, limit=args.limit, installed_root=args.install_root
            )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 2 if result["errors"] else 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
