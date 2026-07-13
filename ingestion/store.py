"""SQLite persistence for raw K Pro chat sessions."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "breadcrumbs.db"
TRANSCRIPTS_DIR = Path(__file__).resolve().parent / "transcripts"
SCHEMA_PATH = ROOT / "schema" / "graph_schema.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate_graph_vocabulary(connection)
    _migrate_findings(connection)
    _migrate_chat_tables(connection)
    _migrate_knowledge_tables(connection)
    return connection


def _migrate_graph_vocabulary(connection: sqlite3.Connection) -> None:
    """Rebuild the two constrained graph tables without losing rows or edges.

    SQLite cannot alter CHECK constraints. Foreign keys are disabled before the
    transaction so renaming/dropping findings cannot cascade-delete the graph.
    The live DDL decides whether a migration is needed, making the operation
    idempotent even for databases created before this repo had migrations.
    """

    findings_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='findings'"
    ).fetchone()
    edges_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='finding_edges'"
    ).fetchone()
    findings_sql = (findings_sql_row[0] if findings_sql_row else "").lower()
    edges_sql = (edges_sql_row[0] if edges_sql_row else "").lower()
    needs_findings = "'open'" not in findings_sql
    needs_edges = "'duplicate_of'" not in edges_sql or "'related-to'" in edges_sql
    if not (needs_findings or needs_edges):
        return

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ALTER TABLE finding_edges RENAME TO __finding_edges_legacy")
        connection.execute("ALTER TABLE findings RENAME TO __findings_legacy")
        connection.execute(
            """
            CREATE TABLE findings (
                id TEXT PRIMARY KEY,
                disease TEXT NOT NULL,
                hypothesis_text TEXT NOT NULL,
                signature TEXT,
                effect TEXT,
                n INTEGER,
                status TEXT NOT NULL CHECK (status IN ('confirmed', 'in-progress', 'abandoned', 'open')),
                author TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                provenance TEXT,
                reason TEXT,
                note TEXT,
                category TEXT REFERENCES topic_categories(id),
                entities TEXT,
                source_session_id TEXT REFERENCES chat_sessions(id),
                source_type TEXT CHECK (source_type IN ('external', 'internal')),
                markdown TEXT,
                resources TEXT
            )
            """
        )
        legacy_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(__findings_legacy)")
        }
        ordered_columns = [
            "id", "disease", "hypothesis_text", "signature", "effect", "n", "status",
            "author", "timestamp", "provenance", "reason", "note", "category", "entities",
            "source_session_id", "source_type", "markdown", "resources",
        ]
        select_columns = [name if name in legacy_columns else f"NULL AS {name}" for name in ordered_columns]
        connection.execute(
            f"INSERT INTO findings ({', '.join(ordered_columns)}) "
            f"SELECT {', '.join(select_columns)} FROM __findings_legacy"
        )
        connection.execute(
            """
            CREATE TABLE finding_edges (
                from_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
                to_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
                relationship TEXT NOT NULL CHECK (
                    relationship IN ('duplicate_of', 'extends', 'related', 'contradicts')
                ),
                created_at TEXT NOT NULL,
                PRIMARY KEY (from_id, to_id, relationship)
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO finding_edges(from_id, to_id, relationship, created_at)
            SELECT from_id, to_id,
                   CASE WHEN relationship = 'related-to' THEN 'related' ELSE relationship END,
                   created_at
            FROM __finding_edges_legacy
            """
        )
        connection.execute("DROP TABLE __finding_edges_legacy")
        connection.execute("DROP TABLE __findings_legacy")
        connection.execute("CREATE INDEX idx_findings_disease ON findings(disease)")
        connection.execute("CREATE INDEX idx_findings_status ON findings(disease, status)")
        connection.execute("CREATE INDEX idx_findings_category ON findings(category)")
        connection.execute("CREATE INDEX idx_findings_source_session ON findings(source_session_id)")
        violations = list(connection.execute("PRAGMA foreign_key_check"))
        if violations:
            raise sqlite3.IntegrityError(f"graph migration left foreign-key violations: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _migrate_findings(connection: sqlite3.Connection) -> None:
    """Add graph fields for databases created before the findings extension."""
    existing = {row[1] for row in connection.execute("PRAGMA table_info(findings)")}
    additions = {
        "category": "TEXT",
        "entities": "TEXT",
        "source_session_id": "TEXT",
        "source_type": "TEXT",
        "markdown": "TEXT",
        "resources": "TEXT",
    }
    for name, definition in additions.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE findings ADD COLUMN {name} {definition}")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_findings_source_session ON findings(source_session_id)")


def _migrate_chat_tables(connection: sqlite3.Connection) -> None:
    """Add chat columns for databases created before section nesting / incremental re-ingest.

    `executescript` runs CREATE TABLE IF NOT EXISTS, so editing graph_schema.sql alone
    never touches an existing breadcrumbs.db. These are pure ALTER ADD COLUMN migrations — no
    table rebuild, so finding_edges' ON DELETE CASCADE is not at risk (see AGENTS.md).
    """
    additions = {
        "chat_sessions": {"updated_at": "TEXT", "researcher": "TEXT"},
        "chat_message_sections": {"parent_id": "TEXT", "path": "TEXT"},
    }
    for table, columns in additions.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_message_sections_parent ON chat_message_sections(parent_id)"
    )


def _migrate_knowledge_tables(connection: sqlite3.Connection) -> None:
    """Add retrieval fields and synchronize the derived FTS5 index without rebuilding authority."""

    existing = {row[1] for row in connection.execute("PRAGMA table_info(knowledge_items)")}
    additions = {
        "aliases": "TEXT NOT NULL DEFAULT '[]'",
        "conditions": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, definition in additions.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE knowledge_items ADD COLUMN {name} {definition}")

    connection.execute(
        """
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
        )
        """
    )
    for trigger in ("knowledge_fts_ai", "knowledge_fts_ad", "knowledge_fts_au"):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    connection.executescript(
        """
        CREATE TRIGGER knowledge_fts_ai AFTER INSERT ON knowledge_items BEGIN
            INSERT INTO knowledge_fts(
                item_id, proposition, rationale, scope, aliases, conditions,
                evidence_quote, action_after, reason
            ) VALUES (
                new.id, new.proposition, new.rationale, new.scope, new.aliases, new.conditions,
                new.evidence_quote, COALESCE(new.action_after, ''), COALESCE(new.reason, '')
            );
        END;

        CREATE TRIGGER knowledge_fts_ad AFTER DELETE ON knowledge_items BEGIN
            DELETE FROM knowledge_fts WHERE item_id = old.id;
        END;

        CREATE TRIGGER knowledge_fts_au AFTER UPDATE ON knowledge_items BEGIN
            DELETE FROM knowledge_fts WHERE item_id = old.id;
            INSERT INTO knowledge_fts(
                item_id, proposition, rationale, scope, aliases, conditions,
                evidence_quote, action_after, reason
            ) VALUES (
                new.id, new.proposition, new.rationale, new.scope, new.aliases, new.conditions,
                new.evidence_quote, COALESCE(new.action_after, ''), COALESCE(new.reason, '')
            );
        END;
        """
    )
    connection.execute(
        "DELETE FROM knowledge_fts WHERE item_id NOT IN (SELECT id FROM knowledge_items)"
    )
    connection.execute(
        """
        INSERT INTO knowledge_fts(
            item_id, proposition, rationale, scope, aliases, conditions,
            evidence_quote, action_after, reason
        )
        SELECT
            k.id, k.proposition, k.rationale, k.scope, k.aliases, k.conditions,
            k.evidence_quote, COALESCE(k.action_after, ''), COALESCE(k.reason, '')
        FROM knowledge_items k
        WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_fts f WHERE f.item_id = k.id
        )
        """
    )


def ingested_revisions(connection: sqlite3.Connection) -> dict[str, str | None]:
    """Map session id -> the K Pro updated_at stamp already stored, for incremental runs."""
    return {
        row["id"]: row["updated_at"]
        for row in connection.execute("SELECT id, updated_at FROM chat_sessions")
    }


def upsert_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    url: str,
    title: str | None,
    raw_payload: Any,
    messages: Iterable[dict[str, Any]],
    updated_at: str | None = None,
    researcher: str | None = None,
) -> None:
    """Replace one session's turn set atomically, retaining its latest raw payload.

    Scoped to this session_id only — ingesting chat B never disturbs chat A. Re-ingesting
    the same chat replaces its turns, which is how a chat that gained new messages in K Pro
    is brought up to date.

    `researcher` names who ran the ingest — K Pro's payload carries only a `role` of
    user/assistant, never a person's identity, so this has to come from the caller (e.g. a
    CLI flag), not from anything scraped. A re-ingest that omits it keeps whatever was
    already stored rather than blanking it out.
    """
    scraped_at = utc_now()
    raw_json = json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None
    turns = list(messages)
    with connection:
        connection.execute(
            """
            INSERT INTO chat_sessions(id, url, title, scraped_at, raw_json, updated_at, researcher)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                url = excluded.url,
                title = excluded.title,
                scraped_at = excluded.scraped_at,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at,
                researcher = COALESCE(excluded.researcher, chat_sessions.researcher)
            """,
            (session_id, url, title, scraped_at, raw_json, updated_at, researcher),
        )
        connection.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        connection.executemany(
            """
            INSERT INTO chat_messages(id, session_id, seq, role, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"{session_id}:{turn['seq']}",
                    session_id,
                    turn["seq"],
                    turn["role"],
                    turn["content"],
                    turn.get("created_at"),
                )
                for turn in turns
            ],
        )
        sections = []
        for turn in turns:
            message_id = f"{session_id}:{turn['seq']}"
            for section in extract_sections(turn["content"]):
                parent_seq = section["parent_seq"]
                sections.append((
                    f"{message_id}:section:{section['seq']}",
                    message_id,
                    section["seq"],
                    section["heading"],
                    section["level"],
                    section["content"],
                    f"{message_id}:section:{parent_seq}" if parent_seq is not None else None,
                    section["path"],
                ))
        connection.executemany(
            """
            INSERT INTO chat_message_sections(id, message_id, seq, heading, level, content, parent_id, path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sections,
        )
    persisted_researcher = connection.execute(
        "SELECT researcher FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()["researcher"]
    write_transcript(session_id, url, title, turns, researcher=persisted_researcher)


def extract_sections(content: str) -> list[dict[str, Any]]:
    """Extract K Pro's visible ##/### Markdown categories from one answer, as a tree.

    K Pro nests its answer: a level-2 heading ("Indication-Specific Summary") is followed
    by level-3 headings, one per indication. Each section carries `seq`, the `parent_seq`
    of the level-2 it sits under (None for a level-2), and a readable `path`
    ("Indication-Specific Summary > Non small cell lung cancer") so topic nodes can be
    built without re-parsing the Markdown.
    """
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    parent_seq: int | None = None
    parent_heading: str | None = None

    def close(section: dict[str, Any]) -> None:
        section["content"] = "\n".join(section.pop("lines")).strip()
        sections.append(section)

    for line in content.splitlines():
        match = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if match:
            if current:
                close(current)
            level = len(match.group(1))
            heading = match.group(2)
            seq = len(sections)
            if level == 2:
                parent_seq, parent_heading = seq, heading
                path = heading
            else:
                path = f"{parent_heading} > {heading}" if parent_heading else heading
            current = {
                "seq": seq,
                "heading": heading,
                "level": level,
                "lines": [],
                "parent_seq": parent_seq if level == 3 else None,
                "path": path,
            }
        elif current:
            current["lines"].append(line)
    if current:
        close(current)
    return sections


def write_transcript(
    session_id: str,
    url: str,
    title: str | None,
    messages: Iterable[dict[str, Any]],
    researcher: str | None = None,
) -> Path:
    """Write a human-readable local view without replacing the canonical database."""
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    heading = title or f"K Pro chat {session_id}"
    sections = [f"# {heading}", "", f"Source: {url}"]
    if researcher:
        sections.append(f"Researcher: {researcher}")
    sections.append("")
    for message in messages:
        speaker = "Researcher" if message["role"] == "user" else "K Pro"
        turn_meta = f"turn {message['seq']}"
        if message.get("created_at"):
            turn_meta += f" · {message['created_at']}"
        sections.extend((f"## {speaker} ({turn_meta})", "", message["content"].strip(), ""))
    path = TRANSCRIPTS_DIR / f"{session_id}.md"
    path.write_text("\n".join(sections), encoding="utf-8")
    return path


def record_error(connection: sqlite3.Connection, session_id: str | None, url: str, error: str) -> None:
    with connection:
        connection.execute(
            "INSERT INTO ingestion_errors(session_id, url, error, created_at) VALUES (?, ?, ?, ?)",
            (session_id, url, error, utc_now()),
        )


_DATASET_COLUMN_HEADER = ("column name", "possible values", "data type", "completeness")
_COMPLETENESS_RE = re.compile(r"^([\d.]+)\s*%$")


def parse_dataset_columns(text: str) -> list[dict[str, Any]]:
    """Parse K Pro's Explore Data column grid, captured as plain page text.

    The grid renders as a flat, repeating run of four values per column (name, possible
    values, data type, completeness %), preceded by one occurrence of those four header
    labels. There is no other structure to lean on once the page is reduced to text — a
    header label that also happened to appear as a column's own data would break this,
    which hasn't occurred for any K Pro dataset table seen so far. A trailing partial group
    (a capture cut off mid-column) is dropped rather than stored as a half-record.
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) >= 4 and tuple(block.lower() for block in blocks[:4]) == _DATASET_COLUMN_HEADER:
        blocks = blocks[4:]
    columns = []
    complete_rows = len(blocks) - (len(blocks) % 4)
    for i in range(0, complete_rows, 4):
        name, possible_values, data_type, completeness_raw = blocks[i : i + 4]
        match = _COMPLETENESS_RE.match(completeness_raw.strip())
        columns.append({
            "column_name": name,
            "possible_values": None if possible_values in ("—", "-") else possible_values,
            "data_type": data_type,
            "completeness_pct": float(match.group(1)) if match else None,
        })
    return columns


_OVERVIEW_FIELDS = {
    "name": "name",
    "source": "source",
    "total patients": "total_patients",
    "total samples": "total_samples",
    "description": "description",
}

# Recognized as label lines (section boundaries) even though only the fields above are
# captured — "Access" and the free-form sections after Description exist purely so their
# value/section text doesn't get swallowed into the field before them.
_OVERVIEW_LABELS = frozenset(_OVERVIEW_FIELDS) | {
    "access",
    "indications",
    "modalities",
    "inclusion & exclusion criteria",
    "available tables",
}


def parse_dataset_overview(text: str) -> dict[str, Any]:
    """Parse K Pro's dataset overview panel (Name/Source/Access/Total patients/.../Description).

    Only the scalar fields the `datasets` table has columns for are extracted; free-form
    sections after Description (Indications, Modalities, Inclusion & Exclusion Criteria)
    are recognized only as terminators, not extracted — they don't fit scalar columns and
    stay in the caller's raw_text for provenance instead.

    This scans line by line for known label lines rather than splitting on blank-line
    blocks: a label and the value above it are not reliably separated by a blank line in a
    browser copy/paste (e.g. "...MOSAIC WINDOW\nSource\n\nOwkin..." — no blank line between
    the Name value and the Source label), so a field's value runs until the next
    recognized label line, wherever it falls.
    """
    lines = text.splitlines()
    overview: dict[str, Any] = {}
    i, n = 0, len(lines)
    while i < n:
        field = _OVERVIEW_FIELDS.get(lines[i].strip().lower())
        if field is None:
            i += 1
            continue
        j = i + 1
        value_lines: list[str] = []
        while j < n and lines[j].strip().lower() not in _OVERVIEW_LABELS:
            value_lines.append(lines[j])
            j += 1
        value = "\n".join(value_lines).strip()
        if value and field not in overview:
            overview[field] = value
        i = j
    for key in ("total_patients", "total_samples"):
        if key in overview:
            try:
                overview[key] = int(overview[key].replace(",", ""))
            except ValueError:
                overview[key] = None
    return overview


_TABLE_COLUMN_COUNT_RE = re.compile(r"^(\d+)\s+columns?$", re.I)


def parse_available_tables(text: str) -> dict[str, int]:
    """Parse the 'Available tables' list (table name, then 'N columns') into declared counts.

    Declared counts are a sanity check, not authoritative: the real count is
    `COUNT(*)` over `dataset_columns` for that table. Compare the two after ingesting a
    table's grid — a mismatch means the scrape only captured part of it.
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    tables: dict[str, int] = {}
    for i in range(len(blocks) - 1):
        match = _TABLE_COLUMN_COUNT_RE.match(blocks[i + 1])
        if match and blocks[i].lower() != "available tables" and not _TABLE_COLUMN_COUNT_RE.match(blocks[i]):
            tables[blocks[i]] = int(match.group(1))
    return tables


def upsert_dataset(
    connection: sqlite3.Connection,
    *,
    dataset_id: str,
    name: str,
    url: str,
    source: str | None = None,
    total_patients: int | None = None,
    total_samples: int | None = None,
    description: str | None = None,
    raw_text: str | None = None,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO datasets(id, name, source, total_patients, total_samples, description, url, scraped_at, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                source = excluded.source,
                total_patients = excluded.total_patients,
                total_samples = excluded.total_samples,
                description = excluded.description,
                url = excluded.url,
                scraped_at = excluded.scraped_at,
                raw_text = excluded.raw_text
            """,
            (dataset_id, name, source, total_patients, total_samples, description, url, utc_now(), raw_text),
        )


def upsert_dataset_columns(
    connection: sqlite3.Connection,
    *,
    dataset_id: str,
    table_name: str,
    columns: Iterable[dict[str, Any]],
) -> None:
    """Replace one table's column set atomically, scoped to (dataset_id, table_name).

    Re-scraping the same table (e.g. after K Pro adds a column) replaces just that table's
    rows; every other table in the same dataset is untouched.
    """
    rows = list(columns)
    with connection:
        connection.execute(
            "DELETE FROM dataset_columns WHERE dataset_id = ? AND table_name = ?",
            (dataset_id, table_name),
        )
        connection.executemany(
            """
            INSERT INTO dataset_columns(id, dataset_id, table_name, column_name, possible_values, data_type, completeness_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"{dataset_id}:{table_name}:{col['column_name']}",
                    dataset_id,
                    table_name,
                    col["column_name"],
                    col.get("possible_values"),
                    col.get("data_type"),
                    col.get("completeness_pct"),
                )
                for col in rows
            ],
        )
