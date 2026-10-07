"""The file side of the upload protocol (batch design §2): part files
written at the server's offset and fsynced before the index moves, a
completion that verifies, hashes and stages, and a free-disk reserve."""
import errno
import hashlib
import io
import logging
import os
import sqlite3
import stat
import struct
import warnings
import zipfile

import pytest

from app.batch import db
from app.batch import uploads as uploads_mod
from app.batch.paths import DataDir
from app.batch.repo import (BatchError, InvalidTransition, Repo, UnknownBatch,
                            UnknownUpload, UploadConflict)
from app.batch.uploads import FINGERPRINT_RE, IMPORTING_MARKER, DiskFull, Uploads
from tests.batch.pdfs import pdf_bytes

CHUNK = 100


class H:
    def __init__(self, tmp_path):
        self.data = DataDir(tmp_path / "data")
        conn = db.connect(self.data.db_path)
        db.migrate(conn)
        self.repo = Repo(conn)
        self.free = 10 ** 12
        self.uploads = Uploads(self.repo, self.data, min_free_bytes=1000,
                               free_bytes=lambda path: self.free)
        self.batch = self.repo.create_batch("b")["id"]

    def create(self, data, name="a.pdf", rel_path="", batch=None):
        return self.uploads.create(batch_id=batch or self.batch, name=name, size=len(data),
                                   last_modified=None, rel_path=rel_path,
                                   fingerprint=hashlib.sha256(data).hexdigest())

    def send(self, data, **kw):
        up = self.create(data, **kw)
        for off in range(up["received"], len(data), CHUNK):
            self.uploads.put(up["upload_id"], off, data[off:off + CHUNK])
        return up["upload_id"]

    def imported(self, data, **kw):
        return self.uploads.complete(self.send(data, **kw))

    def doc_dirs(self):
        return sorted(p.name for p in self.data.docs.iterdir())


@pytest.fixture
def h(tmp_path):
    return H(tmp_path)


def test_create_starts_at_zero_with_an_empty_part_file(h):
    up = h.create(b"x" * 10)
    assert (up["received"], up["state"], up["resumed"]) == (0, "receiving", False)
    assert h.data.part_path(up["upload_id"]).read_bytes() == b""


def test_re_selecting_the_file_resumes_at_the_server_offset(h):
    data = pdf_bytes()
    up = h.create(data)
    h.uploads.put(up["upload_id"], 0, data[:CHUNK])
    again = h.create(data)
    assert (again["upload_id"], again["received"], again["resumed"]) == (up["upload_id"], CHUNK, True)


def test_resuming_an_upload_whose_part_vanished_resumes_at_zero(h):
    """The index counted bytes the disk no longer holds: the resume answers
    the honest offset and the part file is there again for the first chunk."""
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    h.data.part_path(uid).unlink()
    again = h.create(data)
    assert (again["upload_id"], again["received"]) == (uid, 0)
    assert h.data.part_path(uid).read_bytes() == b""
    assert h.uploads.put(uid, 0, data[:CHUNK]) == {"received": CHUNK}


def test_a_resume_racing_the_first_create_keeps_the_row_and_the_part(h, monkeypatch):
    """Tab A creates the upload row; before A creates the part file, tab B
    re-selects the same file, resumes A's row and sends a chunk (creating the
    part). A's exclusive create then finds the file there. That is B's
    upload making progress, not a fault: A must neither delete the row nor
    B's bytes, and must answer the true offset."""
    data = pdf_bytes()
    real = h.repo.create_upload
    state = {"inside": False}

    def racing(**kw):
        row, resumed = real(**kw)
        if not state["inside"]:
            state["inside"] = True
            b = h.create(data)                       # tab B: resumes A's row
            assert (b["upload_id"], b["resumed"]) == (row["id"], True)
            h.uploads.put(b["upload_id"], 0, data[:CHUNK])
        return row, resumed
    monkeypatch.setattr(h.repo, "create_upload", racing)
    a = h.create(data)
    assert (a["resumed"], a["received"]) == (False, CHUNK)
    assert h.repo.get_upload(a["upload_id"])["received"] == CHUNK
    assert h.data.part_path(a["upload_id"]).read_bytes() == data[:CHUNK]


def test_a_failed_new_create_never_takes_a_racing_resume_with_it(h, monkeypatch):
    """Tab A inserts the row; tab B resumes it and sends a chunk; then A's
    own disk check fails. A's failure is A's: B's row and bytes stay."""
    data = pdf_bytes()
    real = h.repo.create_upload
    state = {"inside": False}

    def racing(**kw):
        row, resumed = real(**kw)
        if not state["inside"]:
            state["inside"] = True
            b = h.create(data)
            h.uploads.put(b["upload_id"], 0, data[:CHUNK])
            h.free = 1000                          # the disk now sits at the reserve
        return row, resumed
    monkeypatch.setattr(h.repo, "create_upload", racing)
    with pytest.raises(DiskFull):
        h.create(data)
    (uid,) = [r[0] for r in h.repo._conn.execute("SELECT id FROM upload")]
    assert h.repo.get_upload(uid)["received"] == CHUNK
    assert h.data.part_path(uid).read_bytes() == data[:CHUNK]


def test_a_failed_new_create_removes_only_what_it_made(h, monkeypatch):
    """Its own untouched row goes, and the part only if this call created
    it: a part someone else created is their progress."""
    def failing_dir_sync(path):
        raise OSError(errno.EIO, "boom")
    monkeypatch.setattr(uploads_mod, "_fsync_dir", failing_dir_sync)
    with pytest.raises(BatchError) as e:
        h.create(b"x" * 10)
    assert e.value.status == 500
    assert h.repo._conn.execute("SELECT COUNT(*) FROM upload").fetchone()[0] == 0
    assert list(h.data.uploads.iterdir()) == []

    real_open = open

    def opening(path, mode="r", *a, **kw):
        if mode == "xb":
            with real_open(path, "wb") as f:
                f.write(b"theirs")
        return real_open(path, mode, *a, **kw)
    monkeypatch.setattr(uploads_mod, "open", opening, raising=False)
    with pytest.raises(BatchError):
        h.create(b"y" * 10)
    (part,) = h.data.uploads.iterdir()
    assert part.read_bytes() == b"theirs"


def test_a_part_file_that_appears_under_the_exclusive_create_is_kept(h, monkeypatch):
    """The exclusive create itself fails with FileExistsError (the file
    appeared between the check and the create). That is the part this
    upload needs, holding someone's progress: the row stays, the bytes
    stay, and the create succeeds."""
    real_open = open

    def opening(path, mode="r", *a, **kw):
        if mode == "xb":
            with real_open(path, "wb") as f:
                f.write(b"theirs")
        return real_open(path, mode, *a, **kw)
    monkeypatch.setattr(uploads_mod, "open", opening, raising=False)
    up = h.create(b"x" * 10)
    assert (up["resumed"], up["state"]) == (False, "receiving")
    assert h.repo.get_upload(up["upload_id"])["state"] == "receiving"
    assert h.data.part_path(up["upload_id"]).read_bytes() == b"theirs"


@pytest.mark.parametrize("kw", [
    {"name": "a.exe"}, {"name": "a/b.pdf"}, {"name": "a\x01.pdf"}, {"name": "  "},
    {"name": "exe.‮fdp.pdf"},
    {"fingerprint": "xyz"}, {"fingerprint": "f" * 64 + "\n"}, {"size": -1},
    {"size": True}, {"size": 2 ** 63}, {"rel_path": "../out"},
    {"last_modified": "yesterday"}, {"last_modified": True}, {"last_modified": 1.5},
    {"last_modified": 2 ** 60},
], ids=["extension", "separator", "control", "blank", "bidi", "fingerprint",
        "fingerprint-newline", "size", "size-bool", "size-huge", "rel_path",
        "mtime-str", "mtime-bool", "mtime-float", "mtime-huge"])
def test_create_refuses_what_cannot_be_imported(h, kw):
    args = dict(batch_id=h.batch, name="a.pdf", size=10, last_modified=None, rel_path="",
                fingerprint="f" * 64)
    args.update(kw)
    with pytest.raises(BatchError) as e:
        h.uploads.create(**args)
    # A fixed sentence: never the client's file name echoed back.
    assert e.value.status == 400 and "a.exe" not in str(e.value)
    assert h.repo._conn.execute("SELECT COUNT(*) FROM upload").fetchone()[0] == 0


def test_the_fingerprint_pattern_is_anchored_at_both_ends():
    """`$` also matches before a trailing newline; \\Z does not."""
    assert FINGERPRINT_RE.fullmatch("f" * 64)
    for bad in ("f" * 64 + "\n", "\n" + "f" * 64, "f" * 63, "F" * 64):
        assert not FINGERPRINT_RE.fullmatch(bad)
        assert not FINGERPRINT_RE.match(bad)


def test_unknown_and_started_batches_are_refused(h):
    with pytest.raises(UnknownBatch):
        h.create(b"x", batch="0" * 32)
    with pytest.raises(UnknownBatch):
        h.create(b"x", batch="0" * 32 + "\n")
    h.repo._conn.execute("UPDATE batch SET draft = 0")
    with pytest.raises(InvalidTransition):
        h.create(b"x")


def test_a_chunk_at_the_wrong_offset_is_409_with_the_true_offset(h):
    data = pdf_bytes()
    up = h.create(data)
    with pytest.raises(UploadConflict) as e:
        h.uploads.put(up["upload_id"], CHUNK, data[CHUNK:2 * CHUNK])
    assert e.value.extra == {"received": 0, "state": "receiving"}


@pytest.mark.parametrize("offset", [-1, "0", None, True], ids=["negative", "str", "none", "bool"])
def test_a_malformed_offset_is_400(h, offset):
    uid = h.create(b"x" * 10)["upload_id"]
    with pytest.raises(BatchError) as e:
        h.uploads.put(uid, offset, b"x")
    assert e.value.status == 400


def test_a_malformed_upload_id_is_404(h):
    for bad in ("../x", "0" * 32 + "\n", None):
        with pytest.raises(UnknownUpload):
            h.uploads.put(bad, 0, b"x")
        with pytest.raises(UnknownUpload):
            h.uploads.complete(bad)


def test_a_retried_chunk_after_a_lost_reply_is_not_written_twice(h):
    """The reply to a PUT is lost and the client sends the same chunk again.
    The server already holds it: 409 with the advanced offset, and the file
    holds the chunk once."""
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    assert h.uploads.put(uid, 0, data[:CHUNK]) == {"received": CHUNK}
    with pytest.raises(UploadConflict) as e:
        h.uploads.put(uid, 0, data[:CHUNK])
    assert e.value.extra["received"] == CHUNK
    assert h.data.part_path(uid).read_bytes() == data[:CHUNK]


def test_a_chunk_is_fsynced_before_the_index_moves(h, monkeypatch):
    """A restart loses at most the chunk in flight (decision 21). Were the
    index moved first, a crash could leave it counting bytes the disk never
    got."""
    order = []
    real_fsync, real_received = os.fsync, h.repo.upload_received
    monkeypatch.setattr(uploads_mod.os, "fsync",
                        lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(h.repo, "upload_received",
                        lambda *a: (order.append("index"), real_received(*a))[1])
    uid = h.create(b"x" * 10)["upload_id"]
    order.clear()
    h.uploads.put(uid, 0, b"x" * 10)
    assert order == ["fsync", "index"]


def test_a_chunk_past_the_declared_size_or_an_empty_one_is_400(h):
    uid = h.create(b"x" * 10)["upload_id"]
    for body in (b"x" * 11, b""):
        with pytest.raises(BatchError) as e:
            h.uploads.put(uid, 0, body)
        assert e.value.status == 400
    assert h.repo.get_upload(uid)["received"] == 0


def test_a_lost_part_file_restarts_the_upload_from_zero(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    h.data.part_path(uid).unlink()
    with pytest.raises(UploadConflict) as e:
        h.uploads.put(uid, CHUNK, data[CHUNK:2 * CHUNK])
    assert e.value.extra == {"received": 0, "state": "receiving"}
    assert h.uploads.put(uid, 0, data[:CHUNK]) == {"received": CHUNK}


def test_a_lost_part_file_accepts_offset_zero_straight_away(h):
    """The part is checked BEFORE the offset: a client restarting at 0 is
    right, and is not first sent to the stale offset only to be sent back."""
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    h.data.part_path(uid).unlink()
    assert h.uploads.put(uid, 0, data[:CHUNK]) == {"received": CHUNK}
    assert h.data.part_path(uid).read_bytes() == data[:CHUNK]


@pytest.mark.parametrize("mtime", [None, 0, 1759831200000, -86400000],
                         ids=["none", "epoch", "ms", "before-1970"])
def test_a_browser_modification_time_is_accepted(h, mtime):
    up = h.uploads.create(batch_id=h.batch, name="a.pdf", size=10, last_modified=mtime,
                          rel_path="", fingerprint="f" * 64)
    assert up["received"] == 0


def test_a_part_cut_short_mid_upload_is_resent_from_what_it_holds(h):
    """The part holds fewer bytes than the index counted (cut short outside
    this process). Writing at the index's offset would seek past the end and
    leave a zero-filled hole the drawing would be staged with: the next PUT
    is told where the bytes really end instead."""
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    h.uploads.put(uid, CHUNK, data[CHUNK:2 * CHUNK])
    with open(h.data.part_path(uid), "r+b") as f:
        f.truncate(CHUNK // 2)
    with pytest.raises(UploadConflict) as e:
        h.uploads.put(uid, 2 * CHUNK, data[2 * CHUNK:3 * CHUNK])
    assert e.value.extra == {"received": CHUNK // 2, "state": "receiving"}
    for off in range(CHUNK // 2, len(data), CHUNK):
        h.uploads.put(uid, off, data[off:off + CHUNK])
    (row,) = h.uploads.complete(uid)["documents"]
    assert (h.data.doc_dir(row["id"]) / "input.pdf").read_bytes() == data
    assert h.repo.get(row["id"])["sha256"] == hashlib.sha256(data).hexdigest()


def test_a_part_shared_with_another_file_is_never_written(h):
    """A part with a second link (left behind by a completion that could not
    clean up) shares its inode with a staged drawing: writing through it
    would change that drawing's bytes."""
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    other = h.data.root / "other"
    os.link(h.data.part_path(uid), other)
    with pytest.raises(BatchError) as e:
        h.uploads.put(uid, CHUNK, data[CHUNK:2 * CHUNK])
    assert e.value.status == 500
    assert other.read_bytes() == data[:CHUNK]
    assert h.repo.get_upload(uid)["received"] == CHUNK


def test_an_empty_upload_whose_part_vanished_still_completes(h):
    uid = h.create(b"")["upload_id"]
    h.data.part_path(uid).unlink()
    (row,) = h.uploads.complete(uid)["documents"]
    assert row["unreadable"] and row["size"] == 0
    assert (h.data.doc_dir(row["id"]) / "input.pdf").read_bytes() == b""


def test_without_hard_links_the_drawing_is_copied(h, monkeypatch):
    def no_link(src, dst):
        raise OSError(errno.EXDEV, "cross-device")
    monkeypatch.setattr(uploads_mod.os, "link", no_link)
    data = pdf_bytes()
    (row,) = h.imported(data)["documents"]
    assert (h.data.doc_dir(row["id"]) / "input.pdf").read_bytes() == data


def test_a_link_failure_that_is_not_about_links_is_a_fault_not_a_copy(h, monkeypatch):
    """Only 'this filesystem cannot link' falls back to copying; an I/O
    error is reported, and the completion is rolled back for a retry."""
    def broken(src, dst):
        raise OSError(errno.EIO, "boom", "/secret/path")
    monkeypatch.setattr(uploads_mod.os, "link", broken)
    copied = []
    monkeypatch.setattr(uploads_mod.shutil, "copyfile", lambda *a: copied.append(a))
    data = pdf_bytes()
    uid = h.send(data)
    with pytest.raises(BatchError) as e:
        h.uploads.complete(uid)
    assert e.value.status == 500 and "secret" not in str(e.value)
    assert copied == [] and h.doc_dirs() == []
    assert h.repo.get_upload(uid)["state"] == "receiving"


def test_a_cleanup_that_fails_halfway_leaves_its_dir_marked(h, monkeypatch):
    """A failed completion removes the drawing's files first and its
    .importing marker LAST: if the removal stops halfway, what is left is
    still marked, so the startup sweep can finish it -- never an unmarked
    dir with no row, which nothing would ever remove."""
    import pathlib
    data = pdf_bytes()
    uid = h.send(data)

    def failing(upload_id, docs, ignored):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(h.repo, "finish_completion", failing)
    real_unlink = pathlib.Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "input.pdf":
            raise PermissionError(errno.EACCES, "no")
        return real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    with pytest.raises(sqlite3.OperationalError):
        h.uploads.complete(uid)
    (left,) = h.doc_dirs()
    assert (h.data.docs / left / IMPORTING_MARKER).exists()
    assert h.repo.get_upload(uid)["state"] == "receiving"


def test_low_disk_is_507_and_writes_nothing(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.free = 1000 + CHUNK - 1          # this chunk would dip one byte into the reserve
    with pytest.raises(DiskFull) as e:
        h.uploads.put(uid, 0, data[:CHUNK])
    assert e.value.status == 507
    assert h.repo.get_upload(uid)["received"] == 0
    assert h.data.part_path(uid).read_bytes() == b""
    with pytest.raises(DiskFull):
        h.create(b"y" * 200)
    # The refused upload left no row behind, and no part file.
    assert h.repo._conn.execute("SELECT COUNT(*) FROM upload").fetchone()[0] == 1
    assert [p.name for p in h.data.uploads.iterdir()] == [f"{uid}.part"]


def test_low_disk_on_a_resume_keeps_the_upload(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    h.free = 1000
    with pytest.raises(DiskFull):
        h.create(data)
    assert h.repo.get_upload(uid)["received"] == CHUNK
    assert h.data.part_path(uid).read_bytes() == data[:CHUNK]


@pytest.mark.parametrize("err,status", [(errno.ENOSPC, 507), (errno.EIO, 500)])
def test_a_write_fault_is_reported_by_kind_and_moves_nothing(h, monkeypatch, err, status):
    uid = h.create(b"x" * 10)["upload_id"]

    def failing(fd):
        raise OSError(err, "boom", "/secret/path")
    monkeypatch.setattr(uploads_mod.os, "fsync", failing)
    with pytest.raises(BatchError) as e:
        h.uploads.put(uid, 0, b"x" * 10)
    assert e.value.status == status and "secret" not in str(e.value)
    assert h.repo.get_upload(uid)["received"] == 0


def test_complete_stages_a_pdf_with_its_hash_pages_and_bytes(h):
    data = pdf_bytes(pages=1)
    uid = h.send(data, rel_path="Kunde/Teile")
    done = h.uploads.complete(uid)
    (row,) = done["documents"]
    assert (done["state"], done["ignored"]) == ("complete", [])
    assert (row["file_name"], row["rel_path"], row["size"], row["pages"]) == (
        "a.pdf", "Kunde/Teile", len(data), 1)
    assert row["included"] and not row["unreadable"] and row["oversized"] is False
    d = h.data.doc_dir(row["id"])
    assert (d / "input.pdf").read_bytes() == data
    assert h.repo.get(row["id"])["sha256"] == hashlib.sha256(data).hexdigest()
    assert not (d / IMPORTING_MARKER).exists()
    assert not h.data.part_path(uid).exists()


def test_complete_is_idempotent(h):
    uid = h.send(pdf_bytes())
    first = h.uploads.complete(uid)
    assert h.uploads.complete(uid) == first
    assert len(h.doc_dirs()) == 1


def test_complete_before_every_byte_is_409_with_the_offset(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.put(uid, 0, data[:CHUNK])
    with pytest.raises(UploadConflict) as e:
        h.uploads.complete(uid)
    assert e.value.extra == {"received": CHUNK, "state": "receiving"}


def test_complete_trusts_the_file_over_the_index(h):
    """The index counted bytes the part file no longer holds (cut short
    outside this process). Only bytes on disk count: resend from there."""
    data = pdf_bytes()
    uid = h.send(data)
    with open(h.data.part_path(uid), "r+b") as f:
        f.truncate(CHUNK)
    with pytest.raises(UploadConflict) as e:
        h.uploads.complete(uid)
    assert e.value.extra == {"received": CHUNK, "state": "receiving"}
    for off in range(CHUNK, len(data), CHUNK):
        h.uploads.put(uid, off, data[off:off + CHUNK])
    assert h.uploads.complete(uid)["state"] == "complete"


def test_complete_with_a_part_longer_than_the_file_starts_again(h):
    """put never writes past the declared size, so extra bytes were written
    from outside this process, and nothing on that file can be trusted any
    more. Resetting to `size` would loop the client on 409 forever; the
    honest offset is 0."""
    data = pdf_bytes()
    uid = h.send(data)
    with open(h.data.part_path(uid), "ab") as f:
        f.write(b"junk")
    # The part is started again as a NEW file, never truncated in place: an
    # inode it shares (a staged drawing's) must keep its bytes.
    other = h.data.root / "other"
    os.link(h.data.part_path(uid), other)
    with pytest.raises(UploadConflict) as e:
        h.uploads.complete(uid)
    assert e.value.extra == {"received": 0, "state": "receiving"}
    assert h.data.part_path(uid).read_bytes() == b""
    assert other.read_bytes() == data + b"junk"
    other.unlink()
    assert h.doc_dirs() == []
    for off in range(0, len(data), CHUNK):
        h.uploads.put(uid, off, data[off:off + CHUNK])
    (row,) = h.uploads.complete(uid)["documents"]
    assert (h.data.doc_dir(row["id"]) / "input.pdf").read_bytes() == data


def test_a_failed_completion_leaves_no_drawing_and_can_be_retried(h, monkeypatch):
    """The files are written first, each marked .importing; when the index
    transaction then fails, the files go, the upload is whole again, and a
    retry completes it."""
    data = pdf_bytes()
    uid = h.send(data)
    real = h.repo.finish_completion
    seen = {}

    def failing(upload_id, docs, ignored):
        seen["marked"] = [(h.data.doc_dir(d["id"]) / IMPORTING_MARKER).exists() for d in docs]
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(h.repo, "finish_completion", failing)
    with pytest.raises(sqlite3.OperationalError):
        h.uploads.complete(uid)
    assert seen["marked"] == [True]
    assert h.doc_dirs() == []
    up = h.repo.get_upload(uid)
    assert (up["state"], up["received"]) == ("receiving", len(data))
    assert h.data.part_path(uid).read_bytes() == data
    monkeypatch.setattr(h.repo, "finish_completion", real)
    assert len(h.uploads.complete(uid)["documents"]) == 1


def test_a_storage_fault_during_completion_is_reported_by_kind(h, monkeypatch):
    data = pdf_bytes()
    uid = h.send(data)

    def failing(*a, **kw):
        raise OSError(errno.ENOSPC, "boom", "/secret/path")
    monkeypatch.setattr(uploads_mod, "sha256_file", failing)
    with pytest.raises(DiskFull) as e:
        h.uploads.complete(uid)
    assert "secret" not in str(e.value)
    assert h.doc_dirs() == []
    assert h.repo.get_upload(uid)["state"] == "receiving"


@pytest.mark.parametrize("data", [b"not a pdf at all", b""], ids=["garbage", "empty"])
def test_an_unreadable_file_is_staged_but_never_included(h, data):
    (row,) = h.imported(data)["documents"]
    assert (row["unreadable"], row["included"], row["pages"]) == (True, False, None)


@pytest.mark.parametrize("kw", [{"pages": 2}, {"width": 7000, "height": 5000}],
                         ids=["multi-page", "oversized"])
def test_an_out_of_scope_file_is_staged_excluded(h, kw):
    (row,) = h.imported(pdf_bytes(**kw))["documents"]
    assert (row["out_of_scope"], row["included"], row["allowed_out_of_scope"]) == (True, False, False)


def test_a_second_copy_is_a_duplicate_excluded_by_default(h):
    data = pdf_bytes()
    (first,) = h.imported(data, name="a.pdf")["documents"]
    (second,) = h.imported(data, name="b.pdf")["documents"]
    assert first["included"] and second["duplicate_of"]["id"] == first["id"]
    assert second["included"] is False


def test_delete_removes_an_unfinished_upload_and_its_part(h):
    data = pdf_bytes()
    uid = h.create(data)["upload_id"]
    h.uploads.delete(uid)
    assert not h.data.part_path(uid).exists()
    with pytest.raises(UnknownUpload):
        h.repo.get_upload(uid)
    h.uploads.delete(uid)                      # idempotent
    done = h.imported(data)
    with pytest.raises(InvalidTransition):
        h.uploads.delete(done["upload_id"])


def test_logs_carry_ids_and_counts_never_a_file_name(h, caplog):
    caplog.set_level(logging.DEBUG)
    uid = h.send(pdf_bytes(), name="Geheim-Flansch.pdf", rel_path="Kunde Meier")
    h.uploads.complete(uid)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert uid[:8] in text
    assert "Geheim" not in text and "Meier" not in text and uid not in text


def zip_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    """entries: (name or ZipInfo, data)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def symlink_entry(name):
    i = zipfile.ZipInfo(name)
    i.external_attr = (stat.S_IFLNK | 0o777) << 16
    return i


def test_a_zip_is_unpacked_into_staged_drawings_and_the_rest_listed(h):
    a, b = pdf_bytes(), pdf_bytes(pages=2)
    blob = zip_bytes([
        ("Teile/Welle/w1.pdf", a), ("Teile/Flansch/f1.pdf", b), ("Teile/Stückliste.txt", b"x"),
        ("../../evil.pdf", a), (symlink_entry("link.pdf"), "/etc/passwd"),
        ("kaputt.pdf", b"not a pdf at all"), ("Teile/", b""),
    ])
    done = h.imported(blob, name="Lieferung.zip")
    rows = {r["file_name"]: r for r in done["documents"]}
    assert set(rows) == {"w1.pdf", "f1.pdf", "kaputt.pdf"}
    assert (rows["w1.pdf"]["rel_path"], rows["w1.pdf"]["included"]) == ("Teile/Welle", True)
    assert (rows["f1.pdf"]["pages"], rows["f1.pdf"]["included"]) == (2, False)
    assert rows["kaputt.pdf"]["unreadable"] is True
    assert sorted((i["name"], i["reason"]) for i in done["ignored"]) == [
        ("../../evil.pdf", "unsafe_path"), ("Teile/Stückliste.txt", "not_pdf"),
        ("link.pdf", "symlink")]
    assert (h.data.doc_dir(rows["w1.pdf"]["id"]) / "input.pdf").read_bytes() == a
    assert h.repo.get(rows["w1.pdf"]["id"])["sha256"] == hashlib.sha256(a).hexdigest()
    # Nothing was written but three drawing dirs, each holding only its input.
    assert len(h.doc_dirs()) == 3
    for d in h.data.docs.iterdir():
        assert sorted(p.name for p in d.iterdir()) == ["input.pdf"]
    assert list(h.data.uploads.iterdir()) == []
    assert h.repo.staging(h.batch)["uploads"][0]["ignored"] == done["ignored"]


def test_a_completed_zip_answers_a_repeat_with_what_it_staged(h):
    uid = h.send(zip_bytes([("a.pdf", pdf_bytes()), ("n.txt", b"x")]), name="z.zip")
    first = h.uploads.complete(uid)
    assert h.uploads.complete(uid) == first
    assert len(h.doc_dirs()) == 1


def test_a_zip_from_a_folder_upload_keeps_both_folders(h):
    done = h.imported(zip_bytes([("Welle/w1.pdf", pdf_bytes())]), name="z.zip", rel_path="Kunde")
    assert done["documents"][0]["rel_path"] == "Kunde/Welle"


def test_two_identical_drawings_in_one_zip(h):
    a = pdf_bytes()
    first, second = h.imported(zip_bytes([("x/a.pdf", a), ("y/a.pdf", a)]), name="z.zip")["documents"]
    assert first["included"] and second["duplicate_of"]["id"] == first["id"]
    assert second["included"] is False


def test_two_entries_with_one_name_are_both_staged(h):
    """An archive may repeat a name (zipfile warns, and writes it). Each
    entry is its own drawing in its own dir -- nothing is written to a path
    built from the name, so neither can overwrite the other -- and the user
    sees both, rather than one silently winning."""
    a, b = pdf_bytes(), pdf_bytes(pages=2)
    with pytest.warns(UserWarning):
        blob = zip_bytes([("x/a.pdf", a), ("x/a.pdf", b)])
    rows = h.imported(blob, name="z.zip")["documents"]
    assert [(r["rel_path"], r["file_name"]) for r in rows] == [("x", "a.pdf")] * 2
    assert sorted((h.data.doc_dir(r["id"]) / "input.pdf").read_bytes() for r in rows) == sorted([a, b])


def test_a_corrupt_entry_is_ignored_and_the_rest_staged(h):
    a, b = pdf_bytes(), pdf_bytes()
    blob = bytearray(zip_bytes([("good.pdf", a), ("bad.pdf", b)], compression=zipfile.ZIP_STORED))
    # rindex: a and b are the same bytes, so the LAST copy is bad.pdf's.
    at = bytes(blob).rindex(b) + len(b) // 2
    blob[at] ^= 0xFF                      # stored data no longer matches its CRC
    done = h.imported(bytes(blob), name="z.zip")
    assert [r["file_name"] for r in done["documents"]] == ["good.pdf"]
    assert done["ignored"] == [{"name": "bad.pdf", "reason": "unreadable_entry"}]
    assert len(h.doc_dirs()) == 1


def test_a_file_named_zip_that_is_not_one_is_listed_not_staged(h):
    done = h.imported(b"not a zip", name="z.zip")
    assert (done["state"], done["documents"]) == ("complete", [])
    assert done["ignored"] == [{"name": "z.zip", "reason": "unreadable_zip"}]
    assert h.doc_dirs() == []
    assert h.uploads.complete(done["upload_id"]) == done


def test_low_disk_while_unpacking_is_507_and_leaves_nothing(h):
    blob = zip_bytes([("a.pdf", pdf_bytes()), ("b.pdf", pdf_bytes())])
    uid = h.send(blob, name="z.zip")
    h.free = 1000 + 10                    # room for the archive, not for its contents
    with pytest.raises(DiskFull):
        h.uploads.complete(uid)
    assert h.doc_dirs() == []
    assert h.repo.get_upload(uid)["state"] == "receiving"
    h.free = 10 ** 12
    assert len(h.uploads.complete(uid)["documents"]) == 2


def test_a_zip_bomb_is_stopped_by_the_disk_guard_not_its_header(h, monkeypatch):
    """A few KiB of deflate expand into MiBs. The guard is asked per piece
    with the bytes actually decompressed, so a lying or huge declared size
    can neither sneak past it nor be refused up front (no size limit,
    decision 29): the disk decides."""
    blob = zip_bytes([("bomb.pdf", b"\0" * (8 * uploads_mod.MIB))])
    assert len(blob) < 64 * 1024
    uid = h.send(blob, name="z.zip")
    asked = []
    real = h.uploads.guard_disk
    monkeypatch.setattr(h.uploads, "guard_disk",
                        lambda need=0: (asked.append(need), real(need))[1])
    # A disk that really fills: 3 MiB above the reserve, less what is unpacked.
    h.uploads._free = lambda path: 1000 + 3 * uploads_mod.MIB - sum(
        p.stat().st_size for p in h.data.docs.rglob("*") if p.is_file())
    with pytest.raises(DiskFull):
        h.uploads.complete(uid)
    assert asked and max(asked) <= uploads_mod.MIB
    assert h.doc_dirs() == []
    assert h.repo.get_upload(uid)["state"] == "receiving"


def test_a_write_fault_while_unpacking_is_a_fault_not_an_ignored_entry(h, monkeypatch):
    """Only a damaged ENTRY is ignored. A disk that fails to take its bytes
    is the server's fault: reported by kind, everything rolled back, the
    upload retryable -- never a drawing silently listed as unreadable."""
    uid = h.send(zip_bytes([("a.pdf", pdf_bytes())]), name="z.zip")

    def failing(fd):
        raise OSError(errno.EIO, "boom", "/secret/path")
    monkeypatch.setattr(uploads_mod.os, "fsync", failing)
    with pytest.raises(BatchError) as e:
        h.uploads.complete(uid)
    assert e.value.status == 500 and "secret" not in str(e.value)
    assert h.doc_dirs() == []
    assert h.repo.get_upload(uid)["state"] == "receiving"


def test_a_bzip2_bomb_is_listed_and_never_decompressed(h, monkeypatch):
    """536 bytes of bzip2 hold 64 MiB of zeros, and zipfile would inflate a
    whole compressed read into memory with no output bound -- before the
    disk guard sees a byte. The entry is listed, never opened."""
    pytest.importorskip("bz2")
    blob = zip_bytes([("bomb.pdf", b"\0" * (64 * uploads_mod.MIB))], zipfile.ZIP_BZIP2)
    assert len(blob) < 4096
    uid = h.send(blob, name="z.zip")
    asked = []
    monkeypatch.setattr(h.uploads, "guard_disk", lambda need=0: asked.append(need))
    opened = []
    real_open = zipfile.ZipFile.open
    monkeypatch.setattr(zipfile.ZipFile, "open",
                        lambda self, *a, **kw: (opened.append(a), real_open(self, *a, **kw))[1])
    done = h.uploads.complete(uid)
    assert done["documents"] == []
    assert done["ignored"] == [{"name": "bomb.pdf", "reason": "unsupported_compression"}]
    assert asked == [] and opened == []
    assert h.doc_dirs() == []


def _entry_read_failing(monkeypatch, exc):
    def read(self, n=-1):
        raise exc
    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)


def _decoder_errors():
    import lzma
    errors = [OSError("Invalid data stream"), lzma.LZMAError("corrupt")]
    try:
        from compression.zstd import ZstdError
        errors.append(ZstdError("corrupt"))
    except ImportError:
        pass
    return errors


@pytest.mark.parametrize("exc", _decoder_errors(), ids=lambda e: type(e).__name__)
def test_a_decoder_error_ignores_the_entry(h, monkeypatch, exc):
    """Defence in depth behind classify's codec rule: a decoder's own error
    (bz2 raises a bare OSError with no errno, LZMA and zstd their own types)
    is the ENTRY's damage, listed, never a 500 that blocks the upload."""
    uid = h.send(zip_bytes([("a.pdf", pdf_bytes())]), name="z.zip")
    _entry_read_failing(monkeypatch, exc)
    done = h.uploads.complete(uid)
    assert done["documents"] == [] and h.doc_dirs() == []
    assert done["ignored"] == [{"name": "a.pdf", "reason": "unreadable_entry"}]


def test_a_disk_error_reading_an_entry_is_a_fault_not_the_entry(h, monkeypatch):
    """An OSError WITH an errno is the disk under the archive failing: the
    server's fault, reported by kind and rolled back for a retry."""
    uid = h.send(zip_bytes([("a.pdf", pdf_bytes())]), name="z.zip")
    _entry_read_failing(monkeypatch, OSError(errno.EIO, "boom", "/secret/path"))
    with pytest.raises(BatchError) as e:
        h.uploads.complete(uid)
    assert e.value.status == 500 and "secret" not in str(e.value)
    assert h.repo.get_upload(uid)["state"] == "receiving"


def _overlapping_zip(name):
    """Two central-directory records pointing at ONE local header -- the
    shape of an overlap zip bomb. zipfile warns on one with the entry's
    NAME in the text (to stderr, the server log) and refuses the other."""
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr(name, pdf_bytes())
            zf.writestr(name, pdf_bytes())
    b = bytearray(buf.getvalue())
    pos = b.index(b"PK\x01\x02")
    pos = b.index(b"PK\x01\x02", pos + 1)
    struct.pack_into("<I", b, pos + 42, 0)
    return bytes(b)


def test_entries_sharing_a_local_header_are_ignored_unopened(h, capfd):
    uid = h.send(_overlapping_zip("Kunde-Geheim.pdf"), name="z.zip")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        done = h.uploads.complete(uid)
    assert done["documents"] == [] and h.doc_dirs() == []
    assert done["ignored"] == [{"name": "Kunde-Geheim.pdf", "reason": "unreadable_entry"}] * 2
    assert not any("Geheim" in str(w.message) for w in caught)
    out, err = capfd.readouterr()
    assert "Geheim" not in out and "Geheim" not in err


def test_a_directory_pointing_outside_the_file_is_unreadable_not_a_disk_fault(h):
    """The end record claims the central directory starts 100 bytes later
    than it does. zipfile still finds it (it corrects by the difference), but
    shifts every header_offset by -100: the first entry's becomes negative,
    and seeking there raises OSError(EINVAL) -- WITH an errno, so it would
    read as the disk failing and answer 500 on every retry. An entry whose
    local header lies outside the file is the archive's damage: listed,
    never opened."""
    b = bytearray(zip_bytes([("a.pdf", pdf_bytes())], zipfile.ZIP_STORED))
    eocd = b.rindex(b"PK\x05\x06")
    (cd_offset,) = struct.unpack_from("<I", b, eocd + 16)
    struct.pack_into("<I", b, eocd + 16, cd_offset + 100)
    done = h.imported(bytes(b), name="z.zip")
    assert done["documents"] == [] and h.doc_dirs() == []
    assert done["ignored"] == [{"name": "a.pdf", "reason": "unreadable_entry"}]
    assert list(h.data.uploads.iterdir()) == []


def test_more_than_a_thousand_ignored_entries_are_counted_not_all_listed(h):
    entries = [(f"n{i}.txt", b"") for i in range(1002)] + [("../a.pdf", b"x")]
    done = h.imported(zip_bytes(entries, zipfile.ZIP_STORED), name="z.zip")
    assert len(done["ignored"]) == 1000
    assert done["ignored"][0] == {"name": "n0.txt", "reason": "not_pdf"}
    assert (done["ignored_total"], done["ignored_by_reason"]) == (
        1003, {"not_pdf": 1002, "unsafe_path": 1})
    assert h.uploads.complete(done["upload_id"]) == done
    (up,) = h.repo.staging(h.batch)["uploads"]
    assert (up["ignored"], up["ignored_total"], up["ignored_by_reason"]) == (
        done["ignored"], 1003, done["ignored_by_reason"])


def test_a_pdf_upload_reports_nothing_ignored(h):
    done = h.imported(pdf_bytes())
    assert (done["ignored"], done["ignored_total"], done["ignored_by_reason"]) == ([], 0, {})


def test_zip_entry_names_are_never_logged(h, caplog):
    caplog.set_level(logging.DEBUG)
    blob = zip_bytes([("Kunde Meier/Geheim.pdf", pdf_bytes()), ("Geheim.txt", b"x"),
                      ("Kaputt-Geheim.pdf", b"not a pdf")])
    h.imported(blob, name="Vertraulich.zip")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Geheim" not in text and "Meier" not in text and "Vertraulich" not in text
