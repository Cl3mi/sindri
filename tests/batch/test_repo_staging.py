"""The index side of an import (batch design §1-§2): draft batches, upload
rows with their offsets, and the staged documents a completed upload
becomes. Every step is one guarded transaction, so a retried or racing
request is told where things stand instead of overwriting them."""
import itertools
import sqlite3
import uuid

import pytest

from app.batch import db
from app.batch.repo import InvalidTransition, Repo, UnknownBatch, UnknownUpload, UploadConflict


@pytest.fixture
def repo(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tick = itertools.count()
    return Repo(conn, clock=lambda: f"2026-10-07T00:00:{next(tick):09d}")


def doc(n, **kw):
    d = {"id": f"{n:032x}", "file_name": f"d{n}.pdf", "rel_path": "", "size": 10,
         "sha256": f"{n:064x}", "pages": 1, "oversized": False, "unreadable": False}
    d.update(kw)
    return d


def upload(repo, batch_id, name="a.pdf", rel_path="", size=10, fingerprint=None):
    row, _ = repo.create_upload(batch_id=batch_id, name=name, rel_path=rel_path, size=size,
                                last_modified=None, fingerprint=fingerprint or "f" * 64,
                                kind="pdf")
    return row


def stage(repo, batch_id, *docs, ignored=()):
    """An upload driven through the protocol's index steps, staging `docs`."""
    up = upload(repo, batch_id, fingerprint=uuid.uuid4().hex * 2)
    repo.upload_received(up["id"], 0, up["size"])
    repo.begin_completion(up["id"])
    return repo.finish_completion(up["id"], list(docs), list(ignored))


def test_a_new_batch_is_a_draft(repo):
    b = repo.create_batch("Lieferung 12")
    assert (b["name"], b["draft"]) == ("Lieferung 12", True)
    assert repo.get_batch(b["id"]) == b
    with pytest.raises(UnknownBatch):
        repo.get_batch("0" * 32)


def test_re_selecting_the_same_file_resumes_its_unfinished_upload(repo):
    b = repo.create_batch("b")["id"]
    first = upload(repo, b)
    repo.upload_received(first["id"], 0, 4)
    again, resumed = repo.create_upload(batch_id=b, name="a.pdf", rel_path="", size=10,
                                        last_modified=None, fingerprint="f" * 64, kind="pdf")
    assert (again["id"], again["received"], resumed) == (first["id"], 4, True)


def test_two_identical_files_in_two_folders_never_share_an_upload(repo):
    """Uploaded in parallel, a shared upload would interleave two clients'
    chunks and one of the two files would silently never arrive."""
    b = repo.create_batch("b")["id"]
    assert upload(repo, b, rel_path="x")["id"] != upload(repo, b, rel_path="y")["id"]


def test_a_file_changed_since_is_a_new_upload_when_the_browser_says_so(repo):
    """Same name, size and fingerprint but a different modification time:
    the bytes in between may differ, so it must not continue the old
    upload. A browser that sends no time matches on the rest."""
    b = repo.create_batch("b")["id"]
    kw = dict(batch_id=b, name="a.pdf", rel_path="", size=10, fingerprint="f" * 64, kind="pdf")
    first, _ = repo.create_upload(last_modified=1, **kw)
    changed, resumed = repo.create_upload(last_modified=2, **kw)
    assert changed["id"] != first["id"] and resumed is False
    unknown, resumed = repo.create_upload(last_modified=None, **kw)
    assert (unknown["id"], resumed) == (first["id"], True)


def test_a_resume_counts_as_activity(repo):
    """Or the 7-day sweep could remove an upload while it is being resumed."""
    b = repo.create_batch("b")["id"]
    first = upload(repo, b)
    again, _ = repo.create_upload(batch_id=b, name="a.pdf", rel_path="", size=10,
                                  last_modified=None, fingerprint="f" * 64, kind="pdf")
    assert again["updated_at"] > first["updated_at"]


def test_a_finished_upload_is_never_resumed(repo):
    b = repo.create_batch("b")["id"]
    up = upload(repo, b)
    repo.upload_received(up["id"], 0, 10)
    repo.begin_completion(up["id"])
    repo.finish_completion(up["id"], [doc(1)], [])
    assert upload(repo, b)["id"] != up["id"]


def test_a_started_batch_takes_no_uploads(repo):
    b = repo.create_batch("b")["id"]
    repo._conn.execute("UPDATE batch SET draft = 0 WHERE id = ?", (b,))
    with pytest.raises(InvalidTransition):
        upload(repo, b)


def test_the_offset_moves_only_from_where_it_stands(repo):
    """Two writers racing the same chunk: the second finds the offset
    already moved and is told the real one (409 + received), never
    overwrites it."""
    up = upload(repo, repo.create_batch("b")["id"])
    assert repo.upload_received(up["id"], 0, 4) == 4
    with pytest.raises(UploadConflict) as e:
        repo.upload_received(up["id"], 0, 4)
    assert e.value.status == 409
    assert e.value.extra == {"received": 4, "state": "receiving"}
    with pytest.raises(UnknownUpload):
        repo.upload_received("0" * 32, 0, 4)


def test_completion_needs_every_byte(repo):
    up = upload(repo, repo.create_batch("b")["id"])
    repo.upload_received(up["id"], 0, 4)
    with pytest.raises(UploadConflict) as e:
        repo.begin_completion(up["id"])
    assert e.value.extra == {"received": 4, "state": "receiving"}


def test_completion_stages_the_documents_and_finishes_the_upload_together(repo):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1), doc(2, rel_path="Teile"), ignored=[{"name": "x.txt", "reason": "not_pdf"}])
    assert [r["id"] for r in rows] == [doc(1)["id"], doc(2)["id"]]
    assert {r["state"] for r in rows} == {"staged"} and rows[1]["rel_path"] == "Teile"
    up = repo.get_upload(rows[0]["upload_id"])
    assert up["state"] == "complete"


def test_a_failed_staging_leaves_neither_rows_nor_a_finished_upload(repo):
    """Rows and the upload's `complete` commit together: a fault in the
    middle (here a duplicate id) leaves no half-imported batch, and the
    upload can go back to receiving and be completed again."""
    b = repo.create_batch("b")["id"]
    up = upload(repo, b)
    repo.upload_received(up["id"], 0, 10)
    repo.begin_completion(up["id"])
    with pytest.raises(sqlite3.IntegrityError):
        repo.finish_completion(up["id"], [doc(1), doc(1)], [])
    assert repo._conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 0
    assert repo.get_upload(up["id"])["state"] == "completing"
    repo.abort_completion(up["id"])
    assert repo.get_upload(up["id"])["state"] == "receiving"


@pytest.mark.parametrize("kw,included", [
    ({}, True),
    ({"pages": 3}, False),
    ({"oversized": True}, False),
    ({"unreadable": True, "pages": None, "oversized": None}, False),
    ({"oversized": None}, True),      # never measured is not out of scope
])
def test_include_starts_on_only_for_a_normal_file(repo, kw, included):
    (row,) = stage(repo, repo.create_batch("b")["id"], doc(1, **kw))
    assert row["included"] is included


def test_a_later_copy_is_a_duplicate_excluded_by_default_and_the_original_is_not(repo):
    a = repo.create_batch("Januar")["id"]
    b = repo.create_batch("Februar")["id"]
    (orig,) = stage(repo, a, doc(1, sha256="c" * 64))
    (copy,) = stage(repo, b, doc(2, sha256="c" * 64))
    assert (orig["duplicate_of"], orig["included"]) == (None, True)
    assert copy["included"] is False
    assert copy["duplicate_of"] == {"id": orig["id"], "file_name": "d1.pdf", "batch_id": a,
                                    "batch_name": "Januar", "state": "staged"}


def test_two_identical_files_in_one_upload(repo):
    first, second = stage(repo, repo.create_batch("b")["id"],
                          doc(1, sha256="c" * 64), doc(2, sha256="c" * 64))
    assert first["duplicate_of"] is None and second["duplicate_of"]["id"] == first["id"]


def test_duplicate_of_follows_the_original_but_the_choice_stays(repo):
    """Live: once the original is removed the badge goes. The Include box
    does not flip back on by itself -- that was decided at staging, and
    from then on it is the user's."""
    b = repo.create_batch("b")["id"]
    orig, copy = stage(repo, b, doc(1, sha256="c" * 64), doc(2, sha256="c" * 64))
    repo.delete(orig["id"])
    (row,) = repo.staging(b)["documents"]
    assert (row["id"], row["duplicate_of"], row["included"]) == (copy["id"], None, False)


def test_staging_counts_reconcile(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1), doc(2, pages=2), doc(3, unreadable=True, pages=None, oversized=None),
          doc(4, sha256=f"{1:064x}"))
    upload(repo, b, name="late.pdf")
    view = repo.staging(b)
    c = view["counts"]
    assert c == {"documents": 4, "included": 1, "excluded": 3, "unreadable": 1,
                 "duplicates": 1, "out_of_scope": 1, "uploading": 1}
    assert c["included"] + c["excluded"] == c["documents"] == len(view["documents"])
    assert [u["state"] for u in view["uploads"]] == ["complete", "receiving"]


def test_a_repeated_completion_reports_what_it_staged(repo):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1), ignored=[{"name": "x.txt", "reason": "not_pdf"}])
    done = repo.completed_upload(rows[0]["upload_id"])
    assert [r["id"] for r in done["documents"]] == [rows[0]["id"]]
    assert done["upload"]["ignored"] == [{"name": "x.txt", "reason": "not_pdf"}]


def test_delete_upload_removes_only_an_unfinished_one(repo):
    b = repo.create_batch("b")["id"]
    up = upload(repo, b)
    assert repo.delete_upload(up["id"])["id"] == up["id"]
    assert repo.delete_upload(up["id"]) is None
    rows = stage(repo, b, doc(1))
    with pytest.raises(InvalidTransition):
        repo.delete_upload(rows[0]["upload_id"])
