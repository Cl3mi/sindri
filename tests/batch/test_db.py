"""The document index (batch design §1). Migrations are numbered and atomic:
a crash mid-migration must leave the previous schema, never half of the new
one stamped with the new version."""
import sqlite3

import pytest

from app.batch import db
from app.batch.paths import DataDir


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
