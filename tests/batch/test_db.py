"""The document index (batch design §1). Migrations are numbered and atomic:
a crash mid-migration must leave the previous schema, never half of the new
one stamped with the new version."""
import sqlite3

import pytest

from app.batch import db
from app.batch.paths import DataDir, safe_rel_parts
from app.batch.repo import Repo


def test_migrate_creates_the_schema_and_stamps_its_version(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    assert db.migrate(conn) == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"batch", "document", "setting"} <= tables


def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    assert db.migrate(conn) == len(db.MIGRATIONS)


def test_wal_mode_so_readers_never_wait_on_the_worker(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_failed_migration_leaves_the_previous_version(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [
        "CREATE TABLE one (x INTEGER);",
        "CREATE TABLE two (x INTEGER); CREATE TABLE broken (;",
    ])
    conn = db.connect(tmp_path / "s.db")
    with pytest.raises(sqlite3.Error):
        db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "two" not in tables


def test_a_newer_schema_than_the_code_is_refused(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        db.migrate(conn)


def test_doc_dir_refuses_anything_but_a_hex_id(tmp_path):
    data = DataDir(tmp_path)
    assert data.doc_dir("a" * 32) == tmp_path / "docs" / ("a" * 32)
    for bad in ["../x", "A" * 32, "a" * 31, "a" * 32 + "\n", "", None]:
        with pytest.raises(ValueError):
            data.doc_dir(bad)


def test_migration_2_adds_uploads_and_the_staging_columns(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "upload" in tables
    doc_cols = {r[1] for r in conn.execute("PRAGMA table_info(document)")}
    assert {"upload_id", "size", "unreadable", "included"} <= doc_cols
    up_cols = {r[1] for r in conn.execute("PRAGMA table_info(upload)")}
    assert {"id", "batch_id", "name", "rel_path", "size", "last_modified", "fingerprint",
            "kind", "received", "state", "ignored", "created_at", "updated_at"} <= up_cols


def test_migration_2_turns_a_b1_staged_drawing_into_a_draft_import(tmp_path, monkeypatch):
    """B1 staged an upload as a NON-draft batch of one and queued it with
    /enqueue. From B2 on a drawing is queued only by starting a draft, so a
    drawing staged under B1 and never started must become a draft -- or
    nothing could ever start it. Its Include follows the B2 default (on for
    one page, off for multi-page); `oversized` stays NULL because B1 never
    measured it, and 0 would claim it had. `unreadable` is truthfully 0: B1
    refused every PDF it could not parse."""
    full = db.MIGRATIONS
    conn = db.connect(tmp_path / "s.db")
    monkeypatch.setattr(db, "MIGRATIONS", full[:1])
    db.migrate(conn)
    t = "2026-10-07T00:00:00.000000+00:00"
    for bid, did, state, pages in [("b1", "d1", "staged", 1), ("b2", "d2", "staged", 3),
                                   ("b3", "d3", "queued", 1)]:
        conn.execute("INSERT INTO batch (id, name, draft, created_at) VALUES (?, 'x', 0, ?)", (bid, t))
        conn.execute("INSERT INTO document (id, batch_id, file_name, pages, state, created_at,"
                     " updated_at) VALUES (?, ?, 'x', ?, ?, ?, ?)", (did, bid, pages, state, t, t))
    # NOT monkeypatch.undo(): that would also undo conftest's per-test dirs.
    monkeypatch.setattr(db, "MIGRATIONS", full)
    assert db.migrate(conn) == len(full)
    rows = {r["id"]: dict(r) for r in conn.execute(
        "SELECT d.id, d.included, d.unreadable, d.oversized, b.draft"
        " FROM document d JOIN batch b ON b.id = d.batch_id")}
    assert (rows["d1"]["draft"], rows["d1"]["included"]) == (1, 1)
    assert (rows["d2"]["draft"], rows["d2"]["included"]) == (1, 0)
    assert (rows["d3"]["draft"], rows["d3"]["included"]) == (0, None)
    assert {r["unreadable"] for r in rows.values()} == {0}
    assert {r["oversized"] for r in rows.values()} == {None}


def test_migration_3_gives_a_version_2_index_its_batch_updated_at(tmp_path, monkeypatch):
    """Dev data dirs from earlier B2 commits sit at version 2, whose schema
    has no batch.updated_at -- so the column is a NEW migration, never an
    edit to 2 (append-only even before shipping: the version travels with
    the data dir). A batch from before it was last changed, as far as
    anyone knows, when it was created."""
    full = db.MIGRATIONS
    conn = db.connect(tmp_path / "s.db")
    monkeypatch.setattr(db, "MIGRATIONS", full[:2])
    assert db.migrate(conn) == 2
    assert "updated_at" not in {r[1] for r in conn.execute("PRAGMA table_info(batch)")}
    t = "2026-10-07T00:00:00.000000+00:00"
    conn.execute("INSERT INTO batch (id, name, draft, created_at) VALUES ('b', 'x', 1, ?)", (t,))
    monkeypatch.setattr(db, "MIGRATIONS", full)
    assert db.migrate(conn) == len(full)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(full)
    assert conn.execute("SELECT updated_at FROM batch WHERE id = 'b'").fetchone()[0] == t
    made = Repo(conn).create_batch("new")
    assert conn.execute("SELECT updated_at FROM batch WHERE id = ?",
                        (made["id"],)).fetchone()[0] is not None


@pytest.mark.parametrize("start", [0, 1])
def test_a_fresh_or_b1_index_ends_at_the_latest_version(tmp_path, monkeypatch, start):
    full = db.MIGRATIONS
    conn = db.connect(tmp_path / "s.db")
    monkeypatch.setattr(db, "MIGRATIONS", full[:start])
    db.migrate(conn)
    monkeypatch.setattr(db, "MIGRATIONS", full)
    assert db.migrate(conn) == len(full)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(full)
    assert "updated_at" in {r[1] for r in conn.execute("PRAGMA table_info(batch)")}


def test_part_path_refuses_anything_but_a_hex_id(tmp_path):
    data = DataDir(tmp_path)
    assert data.part_path("a" * 32) == tmp_path / "uploads" / ("a" * 32 + ".part")
    for bad in ["../x", "A" * 32, "a" * 32 + "\n", "", None]:
        with pytest.raises(ValueError):
            data.part_path(bad)


@pytest.mark.parametrize("name,parts", [
    ("", []),
    ("a.pdf", ["a.pdf"]),
    ("Teile/Welle/w1.pdf", ["Teile", "Welle", "w1.pdf"]),
    ("Teile\\Welle\\w1.pdf", ["Teile", "Welle", "w1.pdf"]),
    ("./a//b/", ["a", "b"]),
])
def test_safe_rel_parts_splits_a_relative_path(name, parts):
    assert safe_rel_parts(name) == parts


@pytest.mark.parametrize("name", [
    "/etc/x.pdf", "\\x.pdf", "C:\\x.pdf", "c:x.pdf", "a/../../x.pdf", "..",
    "a\\..\\x.pdf", "a\x00b.pdf", "a\nb.pdf", None,
])
def test_safe_rel_parts_refuses_a_path_that_could_escape(name):
    """Absolute, drive-relative, climbing, or carrying control characters
    (a NUL truncates a name in C code; a newline forges a manifest line)."""
    assert safe_rel_parts(name) is None


def test_migration_2_include_default_is_the_staging_default(tmp_path, monkeypatch):
    """Migration 2 sets Include by exactly the rule a B2 staging applies
    (staging.default_included): off for multi-page and for a duplicate --
    a staged row whose bytes an EARLIER row (by rowid, any state) already
    holds -- and on otherwise. A page count of 0 or NULL is not multi-page,
    so it is not out of scope (`(pages or 0) > 1`)."""
    full = db.MIGRATIONS
    conn = db.connect(tmp_path / "s.db")
    monkeypatch.setattr(db, "MIGRATIONS", full[:1])
    db.migrate(conn)
    t = "2026-10-07T00:00:00.000000+00:00"
    rows = [("orig", "queued", 1, "S"), ("copy", "staged", 1, "S"),
            ("first", "staged", 1, "T"), ("second", "staged", 1, "T"),
            ("p0", "staged", 0, "U"), ("pnull", "staged", None, "V"),
            ("nohash", "staged", 1, None), ("nohash2", "staged", 1, None)]
    for did, state, pages, sha in rows:
        conn.execute("INSERT INTO batch (id, name, draft, created_at) VALUES (?, 'x', 0, ?)",
                     ("b" + did, t))
        conn.execute("INSERT INTO document (id, batch_id, file_name, sha256, pages, state,"
                     " created_at, updated_at) VALUES (?, ?, 'x', ?, ?, ?, ?, ?)",
                     (did, "b" + did, sha, pages, state, t, t))
    monkeypatch.setattr(db, "MIGRATIONS", full)
    db.migrate(conn)
    got = {r["id"]: r["included"] for r in conn.execute("SELECT id, included FROM document")}
    assert got == {"orig": None, "copy": 0, "first": 1, "second": 0,
                   "p0": 1, "pnull": 1, "nohash": 1, "nohash2": 1}


@pytest.mark.parametrize("name", [
    ".. /x.pdf", " ../x.pdf", ".../x.pdf", "a/.../x.pdf", "a/. /x.pdf",
    " a/x.pdf", "a /x.pdf", "a/x.pdf ",
])
def test_safe_rel_parts_refuses_what_windows_turns_into_a_climb(name):
    """Windows strips trailing dots and spaces from a path component, so
    `.. ` and `...` become `..` there. A component that is nothing but dots
    and spaces, or that starts or ends with a space, is refused."""
    assert safe_rel_parts(name) is None


@pytest.mark.parametrize("name", ["a\x85b.pdf", "a\u2028b.pdf", "a\u2029b.pdf",
                                  "a\u202eb.pdf", "a\u200bb.pdf", "a\x7fb.pdf"])
def test_safe_rel_parts_refuses_unicode_line_breaks_and_format_characters(name):
    """NEL and the Unicode line/paragraph separators break a line as surely
    as a newline does; a bidi override (U+202E) makes a shown name lie about
    its extension. Any character of category Cc, Cf, Zl or Zp is refused."""
    assert safe_rel_parts(name) is None


def test_safe_rel_parts_normalises_to_nfc():
    """macOS hands out NFD names; the same folder typed elsewhere is NFC.
    Both must name the same folder in the staging view and the export."""
    nfd = "Teile/We\u0308lle/w1.pdf"
    nfc = "Teile/Wëlle/w1.pdf"
    assert nfd != nfc
    assert safe_rel_parts(nfd) == safe_rel_parts(nfc) == ["Teile", "Wëlle", "w1.pdf"]


def test_migration_4_adds_nullable_progress_columns(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(document)")}
    for name in ("flagged_total", "flagged_open", "unchecked"):
        assert name in cols
        assert cols[name][3] == 0          # not NOT NULL: nullable
        assert cols[name][4] is None       # no default -- NULL, not a lying 0


def test_migration_4_leaves_existing_rows_null(tmp_path, monkeypatch):
    """A row from before this migration has no computed counts yet
    (CLAUDE.md §4: a new field's default must not lie about historical
    data) -- GET /api/documents backfills it lazily, this migration does
    not guess."""
    full = db.MIGRATIONS
    conn = db.connect(tmp_path / "s.db")
    monkeypatch.setattr(db, "MIGRATIONS", full[:3])
    db.migrate(conn)
    t = "2026-10-08T00:00:00.000000+00:00"
    conn.execute("INSERT INTO batch (id, name, draft, created_at, updated_at)"
                 " VALUES ('b', 'x', 0, ?, ?)", (t, t))
    name = "x" + ".pd" + "f"
    conn.execute("INSERT INTO document (id, batch_id, file_name, state, created_at, updated_at)"
                 " VALUES ('d', 'b', ?, 'ready', ?, ?)", (name, t, t))
    monkeypatch.setattr(db, "MIGRATIONS", full)
    assert db.migrate(conn) == len(full)
    row = conn.execute("SELECT flagged_total, flagged_open, unchecked"
                       " FROM document WHERE id = 'd'").fetchone()
    assert tuple(row) == (None, None, None)


def test_the_export_job_migration_adds_the_table(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "export" in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(export)")}
    assert {"id", "document_ids", "state", "done", "total",
            "error_type", "created_at", "updated_at"} <= cols


def test_migrating_twice_is_a_no_op(tmp_path):
    """Confirms the migration count this plan's code was actually written
    against -- a mismatch here means B3's migration landed with a different
    number than this plan assumed, and this task's migration must be
    renumbered before anything else in this file is trusted."""
    conn = db.connect(tmp_path / "s.db")
    first = db.migrate(conn)
    second = db.migrate(conn)
    assert first == second == len(db.MIGRATIONS)


def test_data_dir_has_an_exports_subdirectory(tmp_path):
    data = DataDir(tmp_path)
    assert data.exports.is_dir()
    assert data.export_zip_path("a" * 32) == data.exports / ("a" * 32 + ".zip")
