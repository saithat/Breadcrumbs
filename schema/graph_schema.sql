-- Breadcrumbs graph store: findings as nodes (SQLite, lightest-path per Breadcrumbs.pdf)
-- status: confirmed | in-progress | abandoned | open

-- A controlled registry prevents topic pages from fragmenting through free-text
-- variants. Add approved categories deliberately before writing findings.
CREATE TABLE IF NOT EXISTS topic_categories (
    id          TEXT PRIMARY KEY,
    description TEXT
);

INSERT OR IGNORE INTO topic_categories(id, description)
VALUES ('LUAD-immune', 'LUAD immune-infiltration and survival research');

CREATE TABLE IF NOT EXISTS findings (
    id              TEXT PRIMARY KEY,       -- e.g. "F-118"
    disease         TEXT NOT NULL,          -- e.g. "LUAD"
    hypothesis_text TEXT NOT NULL,          -- natural-language question
    signature       TEXT,                   -- comma-separated gene/marker list, e.g. "CD8A,GZMB,PRF1,GZMK"
    effect          TEXT,                   -- e.g. "HR 1.8, 95% CI 1.2-2.6"
    n               INTEGER,                -- cohort size
    status          TEXT NOT NULL CHECK (status IN ('confirmed', 'in-progress', 'abandoned', 'open')),
    author          TEXT NOT NULL,
    timestamp       TEXT NOT NULL,          -- ISO 8601
    provenance      TEXT,                   -- data source / method, e.g. "TCGA LUAD, cBioPortal, KM+Cox"
    reason          TEXT,                   -- only for status = abandoned
    note            TEXT,                   -- freeform guidance for future researchers
    category        TEXT REFERENCES topic_categories(id),
    entities        TEXT,                   -- JSON array of normalized entity tags
    source_session_id TEXT REFERENCES chat_sessions(id),
    source_type     TEXT CHECK (source_type IN ('external', 'internal')),  -- internal = org, external = published world
    markdown        TEXT,                   -- full markdown writeup of the finding
    resources       TEXT                    -- JSON array of {type:'paper'|'database', citation, url?}; required when source_type='external'
);

CREATE INDEX IF NOT EXISTS idx_findings_disease ON findings(disease);
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(disease, status);

-- Findings form the graph nodes; edges are typed ID-to-ID relationships only.
CREATE TABLE IF NOT EXISTS finding_edges (
    from_id     TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    to_id       TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL CHECK (relationship IN ('duplicate_of', 'extends', 'related', 'contradicts')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, relationship)
);

-- Raw K Pro chat provenance. These records are the source material for later graph
-- extraction; no chat text leaves the local SQLite database in the ingestion step.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    title       TEXT,
    scraped_at  TEXT NOT NULL,
    raw_json    TEXT,
    updated_at  TEXT,          -- K Pro's own last-update stamp; drives incremental re-ingest
    researcher  TEXT           -- who ran the ingest (K Pro carries no per-message author identity)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    created_at  TEXT,
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, seq);

-- Durable interaction knowledge. Proposed candidates remain in the host/UI; only a named
-- researcher's approved version is written here. The exact source quote and scoring samples make
-- each belief change reproducible, while supersedes_id gives continual memory an append-only patch
-- history rather than silently rewriting what the organization previously believed.
CREATE TABLE IF NOT EXISTS knowledge_items (
    id                       TEXT PRIMARY KEY,
    kind                     TEXT NOT NULL CHECK (
        kind IN ('decision', 'constraint', 'exception', 'abandoned', 'belief_revision')
    ),
    proposition              TEXT NOT NULL CHECK (length(trim(proposition)) > 0),
    rationale                TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    scope                    TEXT NOT NULL,  -- JSON object: disease, dataset, method, population, ...
    aliases                  TEXT NOT NULL DEFAULT '[]', -- approved JSON string aliases for recall
    conditions               TEXT NOT NULL DEFAULT '[]', -- approved JSON typed applicability predicates
    evidence_quote           TEXT NOT NULL CHECK (length(trim(evidence_quote)) > 0),
    -- Re-ingest replaces a session's messages inside one transaction. NO ACTION + a deferred
    -- constraint permits delete/reinsert of the same stable message id, while commit still fails
    -- if the approved source actually disappears.
    source_message_id        TEXT NOT NULL REFERENCES chat_messages(id)
                              ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    source_message_hash      TEXT NOT NULL CHECK (length(source_message_hash) = 64),
    source_session_id        TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE RESTRICT,
    prior_samples            TEXT NOT NULL,  -- JSON categorical belief samples
    posterior_samples        TEXT NOT NULL,
    elicitation_model        TEXT NOT NULL,
    elicitation_run_id       TEXT NOT NULL CHECK (length(trim(elicitation_run_id)) > 0),
    scoring_method           TEXT NOT NULL CHECK (scoring_method = 'beta_fractional_jsd_v1'),
    prior_mean               REAL NOT NULL,
    posterior_mean           REAL NOT NULL,
    belief_shift             REAL NOT NULL,
    bayesian_surprise_bits   REAL NOT NULL,
    prior_entropy_bits       REAL NOT NULL,
    posterior_entropy_bits   REAL NOT NULL,
    certainty_gain_bits      REAL NOT NULL,
    action_before            TEXT,           -- JSON object; both action fields are optional together
    action_after             TEXT,
    action_delta             TEXT NOT NULL,  -- JSON list calculated by the server
    prior_action_samples     TEXT,           -- optional JSON categorical action samples
    posterior_action_samples TEXT,
    action_surprise_bits     REAL,
    reason                   TEXT,
    author                   TEXT NOT NULL CHECK (length(trim(author)) > 0),
    approved_by              TEXT NOT NULL CHECK (length(trim(approved_by)) > 0),
    supersedes_id            TEXT REFERENCES knowledge_items(id) ON DELETE RESTRICT,
    created_at               TEXT NOT NULL,
    UNIQUE(source_message_id, kind, proposition),
    UNIQUE(supersedes_id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (
        (kind = 'abandoned' AND reason IS NOT NULL AND length(trim(reason)) > 0)
        OR (kind <> 'abandoned' AND reason IS NULL)
    ),
    CHECK (
        (action_before IS NULL AND action_after IS NULL)
        OR (action_before IS NOT NULL AND action_after IS NOT NULL)
    ),
    CHECK (
        (prior_action_samples IS NULL AND posterior_action_samples IS NULL)
        OR (prior_action_samples IS NOT NULL AND posterior_action_samples IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_knowledge_items_kind_created
    ON knowledge_items(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_source
    ON knowledge_items(source_session_id, source_message_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_supersedes
    ON knowledge_items(supersedes_id);

-- Derived local retrieval index. Triggers and backfill are installed after pure ADD COLUMN
-- migrations in ingestion.store._migrate_knowledge_tables so an older committed database whose
-- knowledge_items table lacks aliases/conditions can be upgraded safely before trigger creation.
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    item_id UNINDEXED,
    proposition,
    rationale,
    scope,
    aliases,
    conditions,
    evidence_quote,
    action_after,
    reason,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- Exact cosine search reads these model-versioned vectors directly. At the current graph size an
-- approximate index would add recall risk without a useful latency benefit. Embeddings are derived
-- from approved patch fields and can be rebuilt from knowledge_items at any time.
CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    item_id      TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    model        TEXT NOT NULL,
    dimensions   INTEGER NOT NULL CHECK (dimensions > 0),
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    vector       BLOB NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (item_id, model)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_model
    ON knowledge_embeddings(model, item_id);

-- Expertise is inferred from source-linked work, not self-declared labels. Exact normalized names
-- create provisional identities; potentially ambiguous fuzzy merges remain a deliberate human task.
CREATE TABLE IF NOT EXISTS people (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
    normalized_name TEXT NOT NULL UNIQUE CHECK (length(trim(normalized_name)) > 0),
    aliases         TEXT NOT NULL DEFAULT '[]',
    org_unit        TEXT,
    identity_status TEXT NOT NULL DEFAULT 'provisional'
                    CHECK (identity_status IN ('provisional', 'verified')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_contributions (
    person_id        TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    artifact_type    TEXT NOT NULL CHECK (artifact_type IN ('finding', 'knowledge')),
    artifact_id      TEXT NOT NULL,
    role             TEXT NOT NULL CHECK (
        role IN ('finding_author', 'knowledge_author', 'knowledge_reviewer')
    ),
    source_session_id TEXT,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (person_id, artifact_type, artifact_id, role)
);

CREATE INDEX IF NOT EXISTS idx_person_contributions_artifact
    ON person_contributions(artifact_type, artifact_id);
CREATE INDEX IF NOT EXISTS idx_person_contributions_person_session
    ON person_contributions(person_id, source_session_id);

-- Identity evidence is graded rather than silently promoted to session ownership. Direct
-- researcher metadata is accepted; source-linked artifact authors and exact-question matches are
-- candidates until a human confirms them. Evidence JSON is canonicalized and hashed so every
-- inference can be reproduced without relying on writing style.
CREATE TABLE IF NOT EXISTS session_identity_candidates (
    session_id        TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    person_id         TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    candidate_name    TEXT NOT NULL CHECK (length(trim(candidate_name)) > 0),
    evidence_type     TEXT NOT NULL CHECK (
        evidence_type IN (
            'session_researcher', 'finding_author', 'knowledge_author',
            'exact_question_match'
        )
    ),
    evidence_strength TEXT NOT NULL CHECK (
        evidence_strength IN ('confirmed', 'supporting', 'weak')
    ),
    status            TEXT NOT NULL CHECK (status IN ('accepted', 'proposed', 'rejected')),
    evidence          TEXT NOT NULL DEFAULT '{}',
    evidence_hash     TEXT NOT NULL CHECK (length(evidence_hash) = 64),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (session_id, person_id, evidence_type)
);

CREATE INDEX IF NOT EXISTS idx_session_identity_candidates_person
    ON session_identity_candidates(person_id, status, evidence_strength);
CREATE INDEX IF NOT EXISTS idx_session_identity_candidates_session
    ON session_identity_candidates(session_id, status, evidence_strength);

-- Asking about a topic is useful organizational activity evidence, but it is not authorship and
-- does not by itself establish expertise. The exact initial user question keeps every edge
-- auditable; sessions without an explicitly supplied researcher create no row.
CREATE TABLE IF NOT EXISTS person_investigations (
    session_id        TEXT PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
    person_id         TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    topic_message_id  TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    topic             TEXT NOT NULL CHECK (length(trim(topic)) > 0),
    topic_message_hash TEXT NOT NULL CHECK (length(topic_message_hash) = 64),
    scope             TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_person_investigations_person
    ON person_investigations(person_id, updated_at DESC);

-- Visible Markdown sections emitted by K Pro (for example, ## Population
-- Overview) are graph-ready categories derived without sending text externally.
-- Sections nest: a level-3 heading belongs to the level-2 heading above it (a
-- per-indication breakdown under "Indication-Specific Summary"). parent_id
-- records that edge so topics form a tree rather than a flat list, and path
-- carries the readable "Parent > Child" label for topic nodes.
CREATE TABLE IF NOT EXISTS chat_message_sections (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    heading     TEXT NOT NULL,
    level       INTEGER NOT NULL CHECK (level IN (2, 3)),
    content     TEXT NOT NULL,
    parent_id   TEXT REFERENCES chat_message_sections(id) ON DELETE CASCADE,
    path        TEXT
);

CREATE INDEX IF NOT EXISTS idx_chat_message_sections_message
    ON chat_message_sections(message_id, seq);

-- The parent_id index is created by store._migrate_chat_tables, not here: on a pre-existing
-- breadcrumbs.db the CREATE TABLE above is a no-op, so parent_id does not exist yet at this point
-- and indexing it would fail. The migration adds the column first, then the index.

-- A failed page/API shape is recorded locally so unsupported K Pro UI changes are
-- visible without storing fabricated turns.
CREATE TABLE IF NOT EXISTS ingestion_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    url         TEXT,
    error       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- K Pro's Explore Data catalog (e.g. https://k.owkin.com/explore-data/patient-data/MOSAIC_WINDOW):
-- which datasets exist, what tables they carry, and each column's declared possible values, data
-- type, and completeness. This is data-availability provenance, not a finding — it lets a
-- finding's free-text `provenance` field and a new hypothesis both be checked against what a
-- dataset actually has, rather than trusted as prose.
CREATE TABLE IF NOT EXISTS datasets (
    id              TEXT PRIMARY KEY,       -- e.g. "mosaic_window"
    name            TEXT NOT NULL,          -- e.g. "MOSAIC WINDOW"
    source          TEXT,                   -- e.g. "Owkin"
    total_patients  INTEGER,
    total_samples   INTEGER,
    description     TEXT,
    url             TEXT NOT NULL,
    scraped_at      TEXT NOT NULL,
    raw_text        TEXT                    -- full captured page text/JSON, for provenance
);

CREATE TABLE IF NOT EXISTS dataset_columns (
    id                TEXT PRIMARY KEY,     -- f"{dataset_id}:{table_name}:{column_name}"
    dataset_id        TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    table_name        TEXT NOT NULL,        -- e.g. "clinical_data_table"
    column_name       TEXT NOT NULL,
    possible_values   TEXT,                 -- category list or numeric range, as displayed
    data_type         TEXT,                 -- category | float | int | bool
    completeness_pct  REAL,                 -- 0-100
    UNIQUE(dataset_id, table_name, column_name)
);

CREATE INDEX IF NOT EXISTS idx_dataset_columns_dataset ON dataset_columns(dataset_id, table_name);
