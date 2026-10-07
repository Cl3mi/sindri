"""SQLite index of batches and documents (batch design §1).

One connection per process, shared by the API threads and the worker under
Repo's lock. WAL so a list request never waits on the worker's writes;
synchronous=FULL because a drawing the server said it queued must survive
power loss, like an acknowledged journal event does. Schema changes are
numbered migrations keyed on PRAGMA user_version -- append an entry, never
edit one that has shipped. Append-only even before a migration ships to a
server: dev data dirs carry the version too, and an edited migration never
reaches a dir already past it. Migration 2 also moves B1's never-started staged
drawings into draft batches, because from B2 on only Start queues a staged
drawing."""
import sqlite3

# ROWID IS DATA HERE. `document` and `upload` have TEXT primary keys, so
# their rowid is implicit -- and duplicate order (repo._STAGING_SELECT, the
# migration-2 Include rule), staging and upload listing order, and the
# queue's tie-break all depend on it. A future migration that rebuilds
# either table (create new, copy, drop, rename) MUST copy it explicitly:
#     INSERT INTO new (rowid, ...) SELECT rowid, ... FROM old
# or the copy renumbers rows and silently changes which file is the original.
# (A plain VACUUM may renumber them too; do not add one without the same care.)
MIGRATIONS = [
    # 1: batches, documents, settings
    """
    CREATE TABLE batch (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        draft INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE document (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL REFERENCES batch(id),
        file_name TEXT NOT NULL,
        rel_path TEXT NOT NULL DEFAULT '',
        sha256 TEXT,
        pages INTEGER,
        oversized INTEGER,
        allowed_out_of_scope INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        error_type TEXT,
        final_revision INTEGER,
        sealed_seq INTEGER NOT NULL DEFAULT 0,
        exported_at TEXT,
        exported_revision INTEGER,
        created_at TEXT NOT NULL,
        queued_at TEXT,
        started_at TEXT,
        ready_at TEXT,
        updated_at TEXT NOT NULL,
        purged_at TEXT
    );
    CREATE INDEX document_queue ON document(state, priority DESC, queued_at);
    CREATE TABLE setting (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 2: chunked uploads and staging (batch B2, design §1-§2)
    """
    CREATE TABLE upload (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL REFERENCES batch(id),
        name TEXT NOT NULL,
        rel_path TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL,
        last_modified INTEGER,
        fingerprint TEXT NOT NULL,
        kind TEXT NOT NULL,
        received INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        ignored TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX upload_resume ON upload(batch_id, fingerprint);
    -- No REFERENCES upload(id): Start deletes a batch's upload rows while
    -- the documents they produced live on, so the link is history, not a
    -- constraint.
    ALTER TABLE document ADD COLUMN upload_id TEXT;
    ALTER TABLE document ADD COLUMN size INTEGER;
    -- DEFAULT 0 is TRUE for every B1 row, not a placeholder: B1 answered an
    -- unparseable PDF with a 400 and never stored it. `oversized` is left
    -- NULL on B1 rows -- B1 never measured it, and 0 would claim it had.
    ALTER TABLE document ADD COLUMN unreadable INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE document ADD COLUMN included INTEGER;
    CREATE INDEX document_sha256 ON document(sha256);
    CREATE INDEX document_batch ON document(batch_id);
    -- From B2 on only Start queues a staged drawing, so a B1 drawing staged
    -- and never queued must sit in a draft or nothing could ever start it.
    UPDATE batch SET draft = 1
        WHERE id IN (SELECT batch_id FROM document WHERE state = 'staged');
    -- Include by the same rule staging.default_included applies at B2
    -- staging: off when out of scope ((pages or 0) > 1, or oversized) or a
    -- duplicate (an EARLIER row by rowid, in any state, holds the same
    -- bytes -- the order a staging-time check would have seen); unreadable
    -- is 0 on every B1 row (above).
    UPDATE document SET included = CASE
            WHEN COALESCE(pages, 0) > 1 OR COALESCE(oversized, 0) THEN 0
            WHEN sha256 IS NOT NULL AND EXISTS (
                SELECT 1 FROM document x
                WHERE x.sha256 = document.sha256 AND x.rowid < document.rowid) THEN 0
            ELSE 1 END
        WHERE state = 'staged';
    """,
    # 3: batch idleness (batch B2 Task 9)
    """
    -- A draft's last change: the empty-draft sweep removes a draft IDLE for
    -- a week, not one merely created a week ago and emptied today. A batch
    -- from before this column was last changed, as far as anyone knows,
    -- when it was created.
    ALTER TABLE batch ADD COLUMN updated_at TEXT;
    UPDATE batch SET updated_at = created_at;
    """,
]


def connect(path) -> sqlite3.Connection:
    # isolation_level=None: transactions are explicit (Repo._tx), so nothing
    # commits behind a caller's back. check_same_thread=False because the
    # worker thread shares this connection under Repo's lock.
    conn = sqlite3.connect(str(path), isolation_level=None,
                           check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > len(MIGRATIONS):
        raise RuntimeError(
            f"database schema {have} is newer than this code ({len(MIGRATIONS)})")
    for n, sql in enumerate(MIGRATIONS[have:], start=have + 1):
        # One script, one transaction: a crash or a bad statement leaves the
        # previous version intact, never half a schema stamped as the new one.
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {n};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)
