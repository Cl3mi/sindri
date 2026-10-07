"""The index side of an import (batch design §1-§2): draft batches, upload
rows with their offsets, and the staged documents a completed upload
becomes. Every step is one guarded transaction, so a retried or racing
request is told where things stand instead of overwriting them."""
import itertools
import sqlite3
import threading
import uuid

import pytest

from app.batch import db
from app.batch.repo import InvalidTransition, Repo, UnknownBatch, UnknownUpload, UploadConflict
from app.batch.staging import clean_batch_name


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


def test_the_offset_never_runs_past_the_file_or_stands_still(repo):
    """An offset beyond the declared size would let begin_completion's
    `received = size` never hold (or hold wrongly); one that does not
    advance is not a chunk. Either way the caller is told the true offset."""
    up = upload(repo, repo.create_batch("b")["id"])
    for bad in (11, 0):
        with pytest.raises(UploadConflict) as e:
            repo.upload_received(up["id"], 0, bad)
        assert e.value.extra == {"received": 0, "state": "receiving"}
    assert repo.upload_received(up["id"], 0, 10) == 10


def test_a_reset_only_moves_the_offset_down(repo):
    """reset_received says "the disk holds less than the index counted"; it
    can never invent bytes. A reset to where the offset already stands (the
    first chunk of an upload whose part file does not exist yet) or above it
    changes nothing and is not an error."""
    up = upload(repo, repo.create_batch("b")["id"])
    repo.upload_received(up["id"], 0, 4)
    for not_down in (8, 4):
        repo.reset_received(up["id"], not_down)
        assert repo.get_upload(up["id"])["received"] == 4
    repo.reset_received(up["id"], 1)
    assert repo.get_upload(up["id"])["received"] == 1


@pytest.mark.parametrize("bad", ["a\x85b", "a b", "a‮b", "a​b", "a\x00b", "", "  ", "x" * 201])
def test_a_batch_name_refuses_what_a_folder_name_refuses(bad):
    """It becomes a folder in the mass-export ZIP (design §4): a NEL or line
    separator forges a line, a bidi override makes the shown name lie."""
    assert clean_batch_name(bad) is None


def test_a_batch_name_is_nfc_and_stripped():
    assert clean_batch_name("  Lieferung München ") == "Lieferung München"


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


def test_a_batch_can_be_renamed_and_listed(repo):
    b = repo.create_batch("Import 2026-10-07 14:30")["id"]
    assert repo.rename_batch(b, "Lieferung 12")["name"] == "Lieferung 12"
    stage(repo, b, doc(1))
    upload(repo, b, name="late.pdf")
    (listed,) = repo.list_batches()
    assert (listed["id"], listed["documents"], listed["uploading"], listed["draft"]) == (b, 1, 1, True)


def test_including_an_out_of_scope_file_is_the_stored_permission(repo):
    (row,) = stage(repo, repo.create_batch("b")["id"], doc(1, pages=4))
    assert (row["included"], row["allowed_out_of_scope"]) == (False, False)
    row = repo.set_included(row["id"], True)
    assert (row["included"], row["allowed_out_of_scope"]) == (True, True)
    row = repo.set_included(row["id"], False)
    assert (row["included"], row["allowed_out_of_scope"]) == (False, False)


def test_an_unreadable_pdf_can_never_be_included(repo):
    (row,) = stage(repo, repo.create_batch("b")["id"],
                   doc(1, unreadable=True, pages=None, oversized=None))
    with pytest.raises(InvalidTransition, match="unreadable"):
        repo.set_included(row["id"], True)
    assert repo.staging(row["batch_id"])["documents"][0]["included"] is False


def test_include_is_refused_once_the_import_has_started(repo):
    b = repo.create_batch("b")["id"]
    (row,) = stage(repo, b, doc(1))
    repo.start_batch(b)
    with pytest.raises(InvalidTransition):
        repo.set_included(row["id"], False)


def test_start_queues_the_included_and_drops_the_rest(repo):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1), doc(2, pages=3), doc(3), doc(4, pages=2),
                 doc(5, unreadable=True, pages=None, oversized=None))
    repo.set_included(rows[3]["id"], True)              # an out-of-scope file, explicitly
    out = repo.start_batch(b)
    ids = [r["id"] for r in rows]
    assert out == {"queued": [ids[0], ids[2], ids[3]], "dropped": [ids[1], ids[4]]}
    assert len(out["queued"]) + len(out["dropped"]) == len(rows)
    assert repo.get_batch(b)["draft"] is False
    view = repo.staging(b)
    assert [r["state"] for r in view["documents"]] == ["queued"] * 3
    assert view["uploads"] == []
    # Every queued out-of-scope drawing carries the permission (decision 26).
    assert all(r["allowed_out_of_scope"] for r in view["documents"] if r["out_of_scope"])
    # The worker takes them in staging order.
    assert [repo.claim_next()["id"] for _ in range(3)] == out["queued"]


def test_start_is_refused_while_a_file_is_still_uploading(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1))
    upload(repo, b, name="late.pdf")
    with pytest.raises(InvalidTransition, match="uploading"):
        repo.start_batch(b)
    assert repo.get_batch(b)["draft"] is True
    assert repo.staging(b)["documents"][0]["state"] == "staged"


def test_start_is_refused_with_nothing_included(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1, pages=2))
    with pytest.raises(InvalidTransition, match="nothing"):
        repo.start_batch(b)
    assert repo.staging(b)["counts"]["documents"] == 1


def test_a_batch_starts_once(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1))
    repo.start_batch(b)
    with pytest.raises(InvalidTransition):
        repo.start_batch(b)
    with pytest.raises(UnknownBatch):
        repo.start_batch("0" * 32)


def test_discarding_a_draft_removes_its_rows(repo):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1))
    late = upload(repo, b, name="late.pdf")
    out = repo.delete_batch(b)
    assert out == {"documents": [rows[0]["id"]], "uploads": [rows[0]["upload_id"], late["id"]]}
    assert repo.delete_batch(b) is None
    with pytest.raises(UnknownBatch):
        repo.get_batch(b)


def test_a_started_batch_cannot_be_discarded(repo):
    b = repo.create_batch("b")["id"]
    stage(repo, b, doc(1))
    repo.start_batch(b)
    with pytest.raises(InvalidTransition):
        repo.delete_batch(b)


def test_removing_the_last_staged_drawing_keeps_the_draft(repo):
    """A draft may still receive files and is continued later (design §2);
    it goes only when discarded, or when it is empty and a week old."""
    b = repo.create_batch("b")["id"]
    (row,) = stage(repo, b, doc(1))
    assert repo.delete(row["id"]) is True
    assert repo.get_batch(b)["draft"] is True


def test_a_drawing_in_a_draft_is_never_queued_one_by_one(repo):
    """Start is where the Include rules live. A per-drawing enqueue would let
    an unreadable or an unconfirmed out-of-scope file past them."""
    (row,) = stage(repo, repo.create_batch("b")["id"], doc(1))
    with pytest.raises(InvalidTransition, match="start"):
        repo.enqueue(row["id"])


@pytest.mark.parametrize("act", ["start_batch", "delete_batch"])
def test_a_draft_holding_a_drawing_past_staged_changes_nothing(repo, act):
    """A draft's drawings are all staged: only Start queues them. Should
    the index ever say otherwise, Start and Discard refuse rather than
    delete a row that may be in the worker's hands."""
    b = repo.create_batch("b")["id"]
    first, second = stage(repo, b, doc(1), doc(2, pages=2))
    repo._conn.execute("UPDATE document SET state = 'extracting' WHERE id = ?", (second["id"],))
    with pytest.raises(InvalidTransition, match="staged"):
        getattr(repo, act)(b)
    assert [r["state"] for r in repo.staging(b)["documents"]] == ["staged", "extracting"]
    assert repo.get_batch(b)["draft"] is True


def test_an_included_out_of_scope_row_without_its_permission_stops_start(repo):
    """Decision 26: an out-of-scope drawing runs only with the permission
    set_included stores. Start checks it instead of trusting `included`, so
    a row that got included any other way is refused, not run."""
    b = repo.create_batch("b")["id"]
    ok, odd = stage(repo, b, doc(1), doc(2, pages=3))
    repo._conn.execute("UPDATE document SET included = 1 WHERE id = ?", (odd["id"],))
    with pytest.raises(InvalidTransition, match="permission"):
        repo.start_batch(b)
    assert [r["state"] for r in repo.staging(b)["documents"]] == ["staged", "staged"]
    assert repo.get_batch(b)["draft"] is True


@pytest.mark.parametrize("act", ["start_batch", "delete_batch"])
def test_start_and_discard_proceed_on_the_snapshot_they_were_given(repo, act):
    b = repo.create_batch("b")["id"]
    rows = stage(repo, b, doc(1), doc(2))
    ids = [r["id"] for r in rows]
    assert getattr(repo, act)(b, expected_ids=list(reversed(ids))) is not None


@pytest.mark.parametrize("act", ["start_batch", "delete_batch"])
def test_a_draft_that_changed_since_the_snapshot_changes_nothing(repo, act):
    """The caller marks each drawing's folder `.importing` from a snapshot
    taken outside the transaction. A completion landing in between would
    add a row whose folder carries no marker and would leak after a crash,
    so a mismatch is refused and the caller reloads."""
    b = repo.create_batch("b")["id"]
    (first,) = stage(repo, b, doc(1))
    snapshot = [first["id"]]
    stage(repo, b, doc(2))
    with pytest.raises(InvalidTransition, match="changed"):
        getattr(repo, act)(b, expected_ids=snapshot)
    view = repo.staging(b)
    assert [r["state"] for r in view["documents"]] == ["staged", "staged"]
    assert len(view["uploads"]) == 2 and view["batch"]["draft"] is True


def test_start_takes_its_queue_time_inside_the_transaction(tmp_path):
    """Queue order must be commit order. With the time read before the
    lock, a Start that read the clock first but committed second would
    still jump ahead of the import that committed before it."""
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    tick = itertools.count()
    tick_lock = threading.Lock()
    armed = {}
    b_done = threading.Event()

    def clock():
        with tick_lock:
            t = f"2026-10-07T00:00:{next(tick):09d}"
        if armed.pop(threading.current_thread().name, False):
            # A pauses after reading the clock until B has finished -- or,
            # if A holds the lock (so B cannot finish), for a short while.
            b_done.wait(0.5)
        return t

    repo = Repo(conn, clock=clock)
    a = repo.create_batch("a")["id"]
    b = repo.create_batch("b")["id"]
    (ra,) = stage(repo, a, doc(1))
    (rb,) = stage(repo, b, doc(2))
    committed = []

    def start(batch_id, name):
        repo.start_batch(batch_id)
        committed.append(batch_id)
        if name == "B":
            b_done.set()

    ta = threading.Thread(target=start, args=(a, "A"), name="A")
    armed["A"] = True
    ta.start()
    while "A" in armed:            # A has read the clock and is pausing
        pass
    tb = threading.Thread(target=start, args=(b, "B"), name="B")
    tb.start()
    ta.join()
    tb.join()
    first = {a: ra["id"], b: rb["id"]}
    assert [repo.claim_next()["id"] for _ in range(2)] == [first[x] for x in committed]
