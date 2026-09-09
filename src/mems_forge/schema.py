from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

SCHEMA_SQL = r'''
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS database_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    manufacturer TEXT,
    publication_code TEXT,
    title TEXT,
    document_type TEXT,
    family TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_revisions (
    revision_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    source_filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    source_size INTEGER NOT NULL,
    page_count INTEGER NOT NULL,
    language_code TEXT,
    edition TEXT,
    revision_label TEXT,
    forge_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_pages (
    page_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES document_revisions(revision_id),
    physical_page_number INTEGER NOT NULL,
    printed_page_label TEXT,
    width REAL,
    height REAL,
    rotation INTEGER,
    content_kind TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(revision_id, physical_page_number)
);

CREATE TABLE IF NOT EXISTS raw_blocks (
    raw_block_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES source_pages(page_id),
    extraction_method TEXT NOT NULL,
    block_kind TEXT,
    source_text TEXT,
    bbox_x0 REAL,
    bbox_y0 REAL,
    bbox_x1 REAL,
    bbox_y1 REAL,
    geometric_order INTEGER,
    confidence REAL,
    engine_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_spans (
    raw_span_id TEXT PRIMARY KEY,
    raw_block_id TEXT NOT NULL REFERENCES raw_blocks(raw_block_id),
    source_text TEXT,
    bbox_x0 REAL,
    bbox_y0 REAL,
    bbox_x1 REAL,
    bbox_y1 REAL,
    font_name TEXT,
    font_size REAL,
    is_bold INTEGER,
    rotation REAL,
    span_order INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS structured_entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    validation_state TEXT NOT NULL DEFAULT 'unresolved',
    parser_version TEXT NOT NULL,
    canonical_key TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS provenance (
    provenance_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES structured_entities(entity_id),
    revision_id TEXT NOT NULL REFERENCES document_revisions(revision_id),
    page_id TEXT NOT NULL REFERENCES source_pages(page_id),
    raw_block_id TEXT REFERENCES raw_blocks(raw_block_id),
    raw_span_id TEXT REFERENCES raw_spans(raw_span_id),
    bbox_x0 REAL,
    bbox_y0 REAL,
    bbox_x1 REAL,
    bbox_y1 REAL,
    evidence_text TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applicability (
    applicability_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES structured_entities(entity_id),
    manufacturer TEXT,
    vehicle_model TEXT,
    vehicle_variant TEXT,
    production_from TEXT,
    production_to TEXT,
    model_year_from INTEGER,
    model_year_to INTEGER,
    engine_code TEXT,
    engine_family TEXT,
    displacement TEXT,
    injection_type TEXT,
    ecu_family TEXT,
    ecu_version TEXT,
    market TEXT,
    transmission TEXT,
    equipment TEXT,
    extra_json TEXT,
    confidence REAL,
    validation_state TEXT NOT NULL DEFAULT 'unresolved',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS relationships (
    relationship_id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL REFERENCES structured_entities(entity_id),
    relation_type TEXT NOT NULL,
    target_entity_id TEXT NOT NULL REFERENCES structured_entities(entity_id),
    validation_state TEXT NOT NULL DEFAULT 'unresolved',
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES source_pages(page_id),
    asset_type TEXT NOT NULL,
    sha256 TEXT,
    width REAL,
    height REAL,
    bbox_x0 REAL,
    bbox_y0 REAL,
    bbox_x1 REAL,
    bbox_y1 REAL,
    extraction_method TEXT,
    storage_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entity_assets (
    entity_id TEXT NOT NULL REFERENCES structured_entities(entity_id),
    asset_id TEXT NOT NULL REFERENCES assets(asset_id),
    relation_type TEXT NOT NULL,
    PRIMARY KEY(entity_id, asset_id, relation_type)
);

CREATE TABLE IF NOT EXISTS review_items (
    review_id TEXT PRIMARY KEY,
    entity_id TEXT REFERENCES structured_entities(entity_id),
    applicability_id TEXT REFERENCES applicability(applicability_id),
    revision_id TEXT REFERENCES document_revisions(revision_id),
    page_id TEXT REFERENCES source_pages(page_id),
    severity TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_text TEXT NOT NULL,
    proposed_value_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    validator_id TEXT,
    decision TEXT,
    corrected_value_json TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS packs (
    pack_id TEXT PRIMARY KEY,
    pack_format_version INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    forge_version TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER,
    validation_state TEXT NOT NULL DEFAULT 'building',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS pack_entities (
    pack_id TEXT NOT NULL REFERENCES packs(pack_id),
    entity_id TEXT NOT NULL REFERENCES structured_entities(entity_id),
    PRIMARY KEY(pack_id, entity_id)
);

CREATE TABLE IF NOT EXISTS pack_sources (
    pack_id TEXT NOT NULL REFERENCES packs(pack_id),
    revision_id TEXT NOT NULL REFERENCES document_revisions(revision_id),
    PRIMARY KEY(pack_id, revision_id)
);

CREATE TABLE IF NOT EXISTS pack_dependencies (
    pack_id TEXT NOT NULL REFERENCES packs(pack_id),
    required_pack_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL,
    min_version TEXT,
    PRIMARY KEY(pack_id, required_pack_id, dependency_type)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    migration_name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS translations (
    translation_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES structured_entities(entity_id),
    language_code TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    translation_state TEXT NOT NULL DEFAULT 'review_required',
    translator_kind TEXT,
    validator_id TEXT,
    validated_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, language_code)
);

CREATE INDEX IF NOT EXISTS idx_revision_document ON document_revisions(document_id);
CREATE INDEX IF NOT EXISTS idx_page_revision ON source_pages(revision_id, physical_page_number);
CREATE INDEX IF NOT EXISTS idx_raw_block_page ON raw_blocks(page_id, geometric_order);
CREATE INDEX IF NOT EXISTS idx_provenance_entity ON provenance(entity_id);
CREATE INDEX IF NOT EXISTS idx_provenance_page ON provenance(page_id);
CREATE INDEX IF NOT EXISTS idx_applicability_entity ON applicability(entity_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status, severity);
CREATE INDEX IF NOT EXISTS idx_entity_type_state ON structured_entities(entity_type, validation_state);
'''


def apply_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.execute(
        "INSERT OR REPLACE INTO database_metadata(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()
