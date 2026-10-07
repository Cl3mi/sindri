"""The document index (batch design §1). Migrations are numbered and atomic:
a crash mid-migration must leave the previous schema, never half of the new
one stamped with the new version."""
import sqlite3

import pytest

from app.batch import db
from app.batch.paths import DataDir, safe_rel_parts


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
    for bad in ["../x", "A" * 32, "a" * 31, "", None]:
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


def test_part_path_refuses_anything_but_a_hex_id(tmp_path):
    data = DataDir(tmp_path)
    assert data.part_path("a" * 32) == tmp_path / "uploads" / ("a" * 32 + ".part")
    for bad in ["../x", "A" * 32, "", None]:
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
