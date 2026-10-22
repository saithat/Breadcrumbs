-- PostgreSQL is Breadcrumbs' only persistence backend.
-- Conversations are provenance; knowledge_items contains human-approved organizational memory.

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          TEXT PRIMARY KEY,
    source_uri  TEXT NOT NULL,
    title       TEXT,
    captured_at TIMESTAMPTZ NOT NULL,
    revision    TEXT,
    actor       TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL CHECK (seq >= 0),
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL CHECK (length(btrim(content)) > 0),
    created_at  TIMESTAMPTZ,
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, seq);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id                     TEXT PRIMARY KEY,
    kind                   TEXT NOT NULL CHECK (
        kind IN ('decision', 'constraint', 'exception', 'abandoned', 'belief_revision')
    ),
    proposition            TEXT NOT NULL CHECK (length(btrim(proposition)) > 0),
    rationale              TEXT NOT NULL CHECK (length(btrim(rationale)) > 0),
    scope                  JSONB NOT NULL,
    aliases                JSONB NOT NULL DEFAULT '[]'::jsonb,
    conditions             JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason                 TEXT,
    evidence_quote         TEXT NOT NULL CHECK (length(btrim(evidence_quote)) > 0),
    source_message_id      TEXT NOT NULL REFERENCES chat_messages(id)
                            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    source_message_hash    TEXT NOT NULL CHECK (length(source_message_hash) = 64),
    source_session_id      TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE RESTRICT,
    prior_samples          JSONB NOT NULL,
    posterior_samples      JSONB NOT NULL,
    elicitation_model      TEXT NOT NULL,
    elicitation_run_id     TEXT NOT NULL CHECK (length(btrim(elicitation_run_id)) > 0),
    scoring_method         TEXT NOT NULL,
    prior_mean             DOUBLE PRECISION NOT NULL,
    posterior_mean         DOUBLE PRECISION NOT NULL,
    belief_shift           DOUBLE PRECISION NOT NULL,
    bayesian_surprise_bits DOUBLE PRECISION NOT NULL,
    surprising             BOOLEAN NOT NULL,
    author                 TEXT NOT NULL CHECK (length(btrim(author)) > 0),
    approved_by            TEXT NOT NULL CHECK (length(btrim(approved_by)) > 0),
    supersedes_id          TEXT REFERENCES knowledge_items(id) ON DELETE RESTRICT,
    search_text            TEXT NOT NULL,
    search_document        TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', search_text)
    ) STORED,
    created_at             TIMESTAMPTZ NOT NULL,
    UNIQUE(source_message_id, kind, proposition),
    UNIQUE(supersedes_id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (
        (kind = 'abandoned' AND reason IS NOT NULL AND length(btrim(reason)) > 0)
        OR (kind <> 'abandoned' AND reason IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_knowledge_items_source
    ON knowledge_items(source_session_id, source_message_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_search
    ON knowledge_items USING GIN(search_document);
