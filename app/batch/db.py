"""SQLite index of batches and documents (batch design §1).

One connection per process, shared by the API threads and the worker under
Repo's lock. WAL so a list request never waits on the worker's writes;
synchronous=FULL because a drawing the server said it queued must survive
power loss, like an acknowledged journal event does. Schema changes are
numbered migrations keyed on PRAGMA user_version -- append an entry, never
edit one that has shipped."""
import sqlite3

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
