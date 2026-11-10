"""Synthetic end-to-end regression tests: no personal data or network."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pymupdf

from mems_forge.package_audit import (
    _safe_file, audit_install, file_sha256, git_blob_sha1,
    inspect_sqlite, render_markdown,
)
from mems_forge.db_tester import search_database


class InstalledCorpusAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source = root / "repo"
        self.installed = root / "installation"
        pdf = self.source / "rave" / "xn" / "TEST990e.PDF"
        pdf.parent.mkdir(parents=True)
        with pymupdf.open() as source:
            source.new_page().insert_text((60, 72), "Technical page 1")
            source.new_page().insert_text((60, 72), "Technical page 2")
            source.save(pdf)
        self.pdf = pdf
        self.baseline = root / "originals.json"
        self.baseline.write_text(json.dumps({
            "schema": 1,
            "origin": {"repository": "fixture", "ref": "test"},
            "documents": [{
                "path": "rave/xn/TEST990e.PDF",
                "size_bytes": pdf.stat().st_size,
                "git_blob_sha1": git_blob_sha1(pdf),
            }],
        }), encoding="utf-8")
        reference = self.installed / "database" / "reference"
        image = reference / "images" / "rave" / "TEST990e_PDF_002.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        self.image = image
        (reference / "mems_reference_seed_1.qz64").write_bytes(b"seed")
        (reference / "research_enrichment.qz64").write_bytes(b"batch")
        (reference / "manifest.json").write_text(json.dumps({
            "database_revision": 21,
            "diagrams": {"example": "images/rave/TEST990e_PDF_002.png"},
            "visual_assets": {
                "technical:2": {
                    "path": "images/rave/TEST990e_PDF_002.png",
                    "sha256": file_sha256(image),
                }
            },
            "research_enrichment_batches": ["research_enrichment.qz64"],
        }), encoding="utf-8")
        library = self.installed / "MEMSLibrary_Pack_001" / "knowledge.sqlite"
        library.parent.mkdir(parents=True)
        with sqlite3.connect(library) as database:
            database.execute(
                "CREATE TABLE memslibrary_search("
                "document_key TEXT, revision_key TEXT, page_number INTEGER, "
                "title TEXT, body TEXT, entity_key TEXT)"
            )
            database.executemany(
                "INSERT INTO memslibrary_search VALUES(?,?,?,?,?,?)",
                [("TEST990e", "revision1", page, f"Page {page}", "Sensor data", str(page))
                 for page in (1, 2)],
            )
            database.execute(
                "CREATE TABLE ravemems_document_revision("
                "revision_key TEXT, document_key TEXT, source_sha256 TEXT)"
            )
            database.execute(
                "INSERT INTO ravemems_document_revision VALUES(?,?,?)",
                ("REV_TEST", "TEST990e", file_sha256(pdf)),
            )
            database.execute(
                "CREATE TABLE ravemems_page("
                "revision_key TEXT, physical_page INTEGER, extraction_status TEXT)"
            )
            database.executemany(
                "INSERT INTO ravemems_page VALUES(?,?,?)",
                [("REV_TEST", 1, "native_text"),
                 ("REV_TEST", 2, "native_text")],
            )
        expert = self.installed / "database" / "expert" / "ia_mems_reference_r20.sqlite"
        expert.parent.mkdir(parents=True)
        with sqlite3.connect(expert) as database:
            database.execute(
                "CREATE TABLE mems_knowledge_item(knowledge_key TEXT, topic TEXT,"
                "source_text TEXT, image_ref TEXT, document TEXT)"
            )
            database.execute(
                "INSERT INTO mems_knowledge_item VALUES(?,?,?,?,?)",
                ("sensor", "oxygen", "Lambda sensor", "images/rave/TEST990e_PDF_002.png",
                 "TEST990e"),
            )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def audit(self):
        return audit_install(self.source, self.installed, self.baseline)

    def test_technical_fixture_does_not_claim_semantic_certification(self):
        report = self.audit()
        self.assertEqual(report["source_documents"]["ok"], 1)
        self.assertEqual(report["source_documents"]["total_pages"], 2)
        self.assertEqual(report["document_mapping"]["unmatched_count"], 0)
        self.assertEqual(report["reference_images_and_enrichments"]["available_images"], 1)
        self.assertEqual(report["reference_images_and_enrichments"]["sha256_verified"], 1)
        self.assertEqual(report["publication_gate"]["state"], "REVIEW_REQUIRED")
        self.assertFalse(report["publication_gate"]["blockers"])
        self.assertIn("Pages des PDF", render_markdown(report))
        mapping = report["document_mapping"]
        self.assertEqual(mapping["physically_verified_pages"], 2)
        self.assertEqual(mapping["complete_physical_page_sets"], 1)
        self.assertEqual(mapping["matched"][0]["source_page_coverage"],
                         "COMPLETE_PAGE_SET")

    def test_physical_page_duplicate_blocks_even_if_row_count_unchanged(self):
        library = self.installed / "MEMSLibrary_Pack_001" / "knowledge.sqlite"
        with sqlite3.connect(library) as db:
            db.execute("UPDATE ravemems_page SET physical_page=1 WHERE physical_page=2")
        report = self.audit()
        self.assertIn("PACK_PDF_PHYSICAL_PAGES_INCOMPLETE:1",
                      report["publication_gate"]["blockers"])
        self.assertEqual(report["document_mapping"]["matched"][0]["missing_physical_pages"], [2])

    def test_missing_physical_page_blocks(self):
        library = self.installed / "MEMSLibrary_Pack_001" / "knowledge.sqlite"
        with sqlite3.connect(library) as db:
            db.execute("DELETE FROM ravemems_page WHERE physical_page=2")
        report = self.audit()
        self.assertIn("PACK_PDF_PHYSICAL_PAGES_INCOMPLETE:1",
                      report["publication_gate"]["blockers"])

    def test_missing_png_is_blocked_and_reported(self):
        self.image.unlink()
        report = self.audit()
        self.assertIn("IMAGES_MISSING:1", report["publication_gate"]["blockers"])

    def test_modified_pdf_detected_by_immutable_baseline(self):
        self.pdf.write_bytes(self.pdf.read_bytes() + b"\nmodified")
        report = self.audit()
        self.assertEqual(report["source_documents"]["ok"], 0)
        self.assertTrue(any(x.startswith("ORIGINAL_INVALID:")
                            for x in report["publication_gate"]["blockers"]))

    def test_missing_original_blocks_publication(self):
        self.pdf.unlink()
        report = self.audit()
        self.assertTrue(any(x.startswith("ORIGINAL_MISSING:")
                            for x in report["publication_gate"]["blockers"]))

    def test_pack_hash_identity_matches_original_pdf_without_filename(self):
        original_sha = file_sha256(self.pdf).upper()
        key = "DOC_PDFSHA256" + original_sha[:16]
        library = self.installed / "MEMSLibrary_Pack_001" / "knowledge.sqlite"
        with sqlite3.connect(library) as db:
            db.execute("UPDATE memslibrary_search SET document_key=?", (key,))
            db.execute("UPDATE ravemems_document_revision SET document_key=?", (key,))
        report = self.audit()
        self.assertEqual(report["document_mapping"]["unmatched_count"], 0)
        self.assertEqual(report["document_mapping"]["matched"][0]["proof"],
                         "SHA256_PREFIX_OF_ORIGINAL_PDF")
        self.assertEqual(report["document_mapping"]["verified_original_pages"], 2)

    def test_additional_pdf_not_in_pack_blocks_exhaustiveness_claim(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.save(other / "OTHER_482.pdf")
        report = audit_install(
            self.source, self.installed, self.baseline, extra_pdf_root=other
        )
        self.assertIn("ADDITIONAL_PDFS_NOT_MAPPED_TO_PACK:1",
                      report["publication_gate"]["blockers"])

    def test_pack_document_name_mismatch_not_hidden_by_equal_counts(self):
        library = self.installed / "MEMSLibrary_Pack_001" / "knowledge.sqlite"
        with sqlite3.connect(library) as db:
            db.execute("UPDATE memslibrary_search SET document_key='OTHER_MANUAL'")
        report = self.audit()
        self.assertEqual(report["document_mapping"]["count_in_pack"], 1)
        self.assertEqual(report["document_mapping"]["unmatched_count"], 1)
        self.assertIn("PDF_NOT_MAPPED_TO_PACK:1", report["publication_gate"]["blockers"])

    def test_python_module_entrypoint_is_wired(self):
        from mems_forge.__main__ import main
        self.assertTrue(callable(main))

    def test_database_tester_returns_provenance_not_ai_text(self):
        library = self.installed / "MEMSLibrary_Pack_001" / "knowledge.sqlite"
        result = search_database(library, "sensor")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["results"][0]["fields"]["document_key"], "TEST990e")
        self.assertEqual(result["results"][0]["fields"]["page_number"], 1)
        self.assertEqual(result["errors"], [])

    def test_database_tester_links_installed_image(self):
        expert = self.installed / "database" / "expert" / "ia_mems_reference_r20.sqlite"
        result = search_database(expert, "Lambda", installed_root=self.installed)
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["results"][0]["source_file_found"])
        self.assertTrue(result["results"][0]["source_file"].endswith(".png"))

    def test_symbolic_rave_image_reference_uses_manifest_not_guessed_filename(self):
        expert = self.installed / "database" / "expert" / "ia_mems_reference_r20.sqlite"
        reference = self.installed / "database" / "reference"
        manifest_path = reference / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["visual_assets"]["rave:TEST990e:2"] = {
            "path": "images/rave/TEST990e_PDF_002.png",
            "sha256": file_sha256(self.image),
        }
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with sqlite3.connect(expert) as db:
            db.execute("UPDATE mems_knowledge_item SET image_ref='TEST990e:2'")
        from mems_forge.package_audit import resolve_image_reference
        linked = resolve_image_reference(self.installed, "TEST990e:2")
        self.assertEqual(linked["status"], "OK")
        self.assertTrue(linked["path"].endswith(".png"))
        result = search_database(expert, "Lambda", installed_root=self.installed)
        self.assertTrue(result["results"][0]["source_file_found"])
        audit = self.audit()
        self.assertEqual(audit["knowledge_image_references"]["total"], 1)
        self.assertEqual(audit["knowledge_image_references"]["verified"], 1)

    def test_symbolic_image_range_expands_and_opens_both_images(self):
        from mems_forge.package_audit import expand_image_references
        from mems_forge.db_tester import search_database
        self.assertEqual(
            expand_image_references("RCL0194ENG:20.1-20.2"),
            ["RCL0194ENG:20.1", "RCL0194ENG:20.2"],
        )
        self.assertEqual(
            expand_image_references("RCL0194ENG:20.2,39.1"),
            ["RCL0194ENG:20.2", "RCL0194ENG:39.1"],
        )
        reference = self.installed / "database" / "reference"
        manifest_path = reference / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for number in ("20.1", "20.2"):
            data["visual_assets"]["rave:TEST990e:" + number] = {
                "path": "images/rave/TEST990e_PDF_002.png",
                "sha256": file_sha256(self.image),
            }
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        expert = self.installed / "database" / "expert" / "ia_mems_reference_r20.sqlite"
        with sqlite3.connect(expert) as db:
            db.execute(
                "UPDATE mems_knowledge_item SET image_ref='TEST990e:20.1-20.2'"
            )
        direct = search_database(expert, "Lambda", installed_root=self.installed)
        row = direct["results"][0]
        self.assertTrue(row["source_file_found"])
        self.assertEqual(len(row["source_files"]), 2)
        self.assertEqual(self.audit()["knowledge_image_references"]["verified"], 1)

    def test_partial_diagram_set_reports_only_the_missing_component(self):
        reference = self.installed / "database" / "reference"
        manifest_path = reference / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["visual_assets"]["rave:TEST990e:20.1"] = {
            "path": "images/rave/TEST990e_PDF_002.png",
            "sha256": file_sha256(self.image),
        }
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        expert = self.installed / "database" / "expert" / "ia_mems_reference_r20.sqlite"
        with sqlite3.connect(expert) as db:
            db.execute(
                "UPDATE mems_knowledge_item SET image_ref='TEST990e:20.1,39.1'"
            )
        report = self.audit()
        unresolved = report["knowledge_image_references"]["unresolved"]
        self.assertEqual(unresolved[0]["missing_parts"], ["TEST990e:39.1"])
        self.assertEqual(unresolved[0]["available_parts"], ["TEST990e:20.1"])

    def test_missing_symbolic_image_reference_is_a_blocker(self):
        expert = self.installed / "database" / "expert" / "ia_mems_reference_r20.sqlite"
        with sqlite3.connect(expert) as db:
            db.execute("UPDATE mems_knowledge_item SET image_ref='NOT_LISTED:42'")
        result = self.audit()
        self.assertIn("EXPERT_IMAGE_REFERENCES_NOT_OPENABLE:1",
                      result["publication_gate"]["blockers"])

    def test_combined_database_query_does_not_miss_expert_only_answer(self):
        from mems_forge.db_tester import search_installed_databases
        result = search_installed_databases(self.installed, "Lambda", limit=15)
        self.assertGreaterEqual(len(result["bases"]), 2)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["source_database"], "expert_database")

    def test_outside_reference_path_rejected(self):
        reference = self.installed / "database" / "reference"
        self.assertIsNone(_safe_file(reference, "../../private.pdf"))
        self.assertIsNone(_safe_file(reference, "/etc/passwd"))
        self.assertIsNone(_safe_file(reference, "C:/Windows/secrets.txt"))
        self.assertIsNone(_safe_file(reference, "images/../private.png"))

    def test_missing_sqlite_does_not_create_empty_database(self):
        location = self.installed / "missing.sqlite"
        report = inspect_sqlite(location)
        self.assertEqual(report["status"], "MISSING")
        self.assertFalse(location.exists())

    def test_reference_hash_mismatch_blocks(self):
        self.image.write_bytes(b"changed image")
        report = self.audit()
        self.assertIn("IMAGES_HASH_MISMATCH:1", report["publication_gate"]["blockers"])

    def test_reference_enrichment_missing_blocks(self):
        (self.installed / "database" / "reference" /
         "research_enrichment.qz64").unlink()
        report = self.audit()
        self.assertIn("ENRICHMENT_FILES_MISSING:1",
                      report["publication_gate"]["blockers"])


if __name__ == "__main__":
    unittest.main()
