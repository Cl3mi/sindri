"""Chunked, resumable upload protocol (batch design §2) -- the file side;
the index side is Repo.

    create   -> an `upload` row in a draft batch and an empty part file. An
                unfinished upload of the same file is RETURNED instead, with
                its offset: re-selecting the files resumes (decision 21).
    put      -> bytes at `offset`, accepted only when offset == received,
                fsynced BEFORE the index moves -- so a retry after a lost
                reply is answered 409 + the true offset, never written twice,
                and a restart loses at most the chunk in flight.
    complete -> verify the bytes ON DISK against the declared size, then
                stage: a PDF becomes one staged document (full SHA-256,
                pages, oversized, unreadable), a ZIP one per PDF inside it
                (unzip.py decides which entries; everything else is listed
                as ignored). Idempotent: a repeated call returns what was
                staged.

A free-disk reserve (SINDRI_MIN_FREE_MB) answers 507 at every step that
writes, so an import can never fill the disk under a review journal. It is a
guard, not a limit: no file is refused for its size (decision 29).

Logs: ids[:8], counts and exception TYPES only -- never a file name or a
folder (CLAUDE.md §1)."""
import errno
import hashlib
import logging
import lzma
import os
import re
import shutil
import threading
import unicodedata
import uuid
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.batch.paths import DOC_ID_RE, has_refused_chars, safe_rel_parts
from app.batch.pdf_facts import pdf_facts
from app.batch.repo import BatchError, UnknownBatch, UnknownUpload, UploadConflict
from app.batch.unzip import (Ignored, classify, ignored as ignored_entry,
                             shared_headers, summarise_ignored)

log = logging.getLogger(__name__)

MIB = 1024 * 1024
# The most one PUT may carry. It bounds the memory of ONE REQUEST, not the
# size of a file: a file of any size arrives in as many chunks as it needs
# (decision 29 -- no upload limits). The browser sends 8 MiB (upload.js).
MAX_CHUNK = 64 * MIB
# \A...\Z and fullmatch, like paths.DOC_ID_RE: `$` also matches before a
# trailing newline, and a fingerprint is part of the resume key.
FINGERPRINT_RE = re.compile(r"\A[0-9a-f]{64}\Z")
# Marks a docs/<id>/ whose row an index transaction is creating (a
# completion) or may remove (Start's dropped rows, Discard). Only a marked
# dir with no row is ever swept at startup, and a marked dir WITH a row is
# only unmarked -- so a lost or replaced index can never make the sweep
# delete real drawings.
IMPORTING_MARKER = ".importing"
# Design §2: unfinished uploads untouched this long are removed. Empty drafts
# go after the same age.
STALE_AFTER = timedelta(days=7)
_COPY_PIECE = MIB
# What reading a damaged entry can raise: a CRC mismatch, a broken deflate
# stream, a truncated archive, an unsupported compression method, an
# encrypted entry unzip.py did not see flagged, a decoder's own error. Each
# makes that ONE entry ignored, never the whole import. (unzip.classify lets
# only stored/deflate through; the LZMA and zstd errors are defence in depth
# should that rule ever widen.) OSError is handled apart, in _copy_entry.
_ENTRY_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError,
                 RuntimeError, ValueError, lzma.LZMAError)
try:
    from compression.zstd import ZstdError
    _ENTRY_ERRORS += (ZstdError,)
except ImportError:                      # before Python 3.14
    pass
# The name is stored and shown, and becomes a file name in the mass-export
# ZIP (design §4); a separator or control character has no business in it.
# (has_refused_chars adds format and line-separator characters: a bidi
# override would make a shown name lie about its extension.)
_NAME_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]")
# Importable kinds by extension. The browser filters too; this is the
# server's word.
_KINDS = {".pdf": "pdf", ".zip": "zip"}
# SQLite stores a signed 64-bit integer: a bigger size would fail inside the
# insert as a 500 instead of being refused as a 400. Not a size LIMIT
# (decision 29) -- no disk holds 8 EiB.
_MAX_SIZE = 2 ** 63
# A browser's File.lastModified is milliseconds since the epoch, a JS
# number: past the safe-integer range it is not a time a browser sent.
# Before 1970 is allowed -- a wrong clock is no reason to refuse a drawing.
_MAX_MTIME = 2 ** 53
# os.link errors that mean "this filesystem cannot hard-link here", where a
# copy is the right fallback. Anything else (EIO, ENOSPC, a vanished part)
# is a fault, and copying would only hide it.
_NO_LINK = frozenset(e for e in (errno.EXDEV, errno.EPERM, errno.EMLINK,
                                 getattr(errno, "ENOTSUP", None),
                                 getattr(errno, "EOPNOTSUPP", None)) if e is not None)


class DiskFull(BatchError):
    status = 507

    def __init__(self):
        super().__init__("not enough free disk space on the server")


class _EntryUnreadable(Exception):
    pass


def _entry_or_disk(e: OSError):
    """Raise what an OSError while READING an entry means. A real I/O fault
    carries an errno -- the disk under the archive failed, the server's
    fault, rolled back and retried. A decoder's complaint does not (bz2
    raises a bare OSError("Invalid data stream")) -- the entry's damage."""
    if e.errno is not None:
        raise e
    raise _EntryUnreadable() from e


class StorageFault(BatchError):
    status = 500

    def __init__(self):
        super().__init__("could not store the upload")


def free_bytes(path) -> int:
    return shutil.disk_usage(path).free


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while piece := f.read(_COPY_PIECE):
            h.update(piece)
    return h.hexdigest()


def _fsync_dir(path) -> None:
    # A new file's NAME is durable only once its directory is synced.
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path) -> None:
    with open(path, "rb+") as f:
        os.fsync(f.fileno())


def _is_count(n) -> bool:
    # bool is an int subclass; True is not a byte count or an offset.
    return isinstance(n, int) and not isinstance(n, bool) and n >= 0


def upload_kind(name: str) -> Optional[str]:
    return _KINDS.get(os.path.splitext(name)[1].lower())


class Uploads:
    def __init__(self, repo, data, min_free_bytes: int, free_bytes=free_bytes):
        self.repo = repo
        self.data = data
        self.min_free_bytes = min_free_bytes
        self._free = free_bytes
        # One lock per upload, held across check-offset / write / fsync /
        # move-offset, across part-file creation and across completion: a
        # client retry racing its own first attempt, or a second tab
        # resuming the same file, is serialised, so a check and the write
        # it guards cannot interleave. Grows by one small lock per upload,
        # like app.main's export locks (single process, decision 5).
        self._locks = {}
        self._locks_guard = threading.Lock()
        # One lock per batch, held by a completion from its commit until its
        # markers are off, and by Start / Discard from their snapshot until
        # their commit (see start_batch). Lock order: an upload's lock may
        # be held when a batch's is taken, never the other way round.
        self._batch_locks = {}

    def _lock(self, upload_id) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(upload_id, threading.Lock())

    def _batch_lock(self, batch_id) -> threading.Lock:
        with self._locks_guard:
            return self._batch_locks.setdefault(batch_id, threading.Lock())

    def _part(self, upload_id):
        try:
            return self.data.part_path(upload_id)
        except ValueError:
            raise UnknownUpload("no such upload") from None

    def free_mb(self) -> int:
        return self._free(self.data.root) // MIB

    def guard_disk(self, need: int = 0) -> None:
        """507 when writing `need` more bytes would leave less than the
        reserve."""
        if self._free(self.data.root) - need < self.min_free_bytes:
            raise DiskFull()

    @staticmethod
    def _storage_error(e: OSError, upload_id: str) -> BatchError:
        # TYPE and id[:8] only: an OSError's text names the path (CLAUDE.md §1).
        log.warning("upload %s not stored: %s", upload_id[:8], type(e).__name__)
        return DiskFull() if e.errno == errno.ENOSPC else StorageFault()

    # ----- create ---------------------------------------------------------
    def create(self, *, batch_id, name, size, last_modified, rel_path, fingerprint) -> dict:
        if not isinstance(name, str):
            raise BatchError("not a usable file name")
        # NFC like safe_rel_parts' folders: a macOS (NFD) name and the same
        # name typed elsewhere are one name -- and one resume key.
        name = unicodedata.normalize("NFC", name).strip()
        if not name or _NAME_UNSAFE.search(name) or has_refused_chars(name):
            raise BatchError("not a usable file name")
        kind = upload_kind(name)
        if kind is None:
            raise BatchError("only PDF and ZIP files can be imported")
        if not _is_count(size) or size >= _MAX_SIZE:
            raise BatchError("size must be a whole number of bytes")
        if last_modified is not None and not (
                isinstance(last_modified, int) and not isinstance(last_modified, bool)
                and abs(last_modified) <= _MAX_MTIME):
            raise BatchError("last_modified must be a time in milliseconds")
        if not isinstance(fingerprint, str) or not FINGERPRINT_RE.fullmatch(fingerprint):
            raise BatchError("fingerprint must be 64 lowercase hex characters")
        parts = safe_rel_parts(rel_path or "")
        if parts is None:
            raise BatchError("unsafe folder path")
        if not isinstance(batch_id, str) or not DOC_ID_RE.fullmatch(batch_id):
            raise UnknownBatch("no such batch")
        row, resumed = self.repo.create_upload(
            batch_id=batch_id, name=name, rel_path="/".join(parts), size=size,
            last_modified=last_modified, fingerprint=fingerprint, kind=kind)
        uid = row["id"]
        # Everything below runs under the upload's lock. Between the row's
        # commit and this point another tab may already have resumed the row
        # and sent chunks: the part file is then its progress, never ours to
        # remove, so the row is re-read and its offset answered as it stands.
        with self._lock(uid):
            part = self.data.part_path(uid)
            made = False
            try:
                row = self._sync_to_part(uid, part)
                # What is still to come, not the whole file: a resumed
                # upload needs room only for the rest.
                self.guard_disk(row["size"] - row["received"])
                if row["state"] == "receiving" and not part.exists():
                    try:
                        with open(part, "xb"):
                            pass
                        made = True
                    except FileExistsError:
                        # Someone created it first (a racing resume's PUT):
                        # that IS the part file this upload needs.
                        pass
                    _fsync_dir(part.parent)
            except BaseException as e:
                # Take back only what THIS call made: the row if nobody has
                # resumed or fed it since, and the part only if this call
                # created it. Anything else is another tab's progress.
                try:
                    if not resumed and self.repo.delete_untouched_upload(uid) and made:
                        part.unlink(missing_ok=True)
                except Exception as cleanup_error:
                    log.warning("upload %s: failed create not taken back: %s",
                                uid[:8], type(cleanup_error).__name__)
                if isinstance(e, OSError):
                    raise self._storage_error(e, uid) from None
                raise
        log.info("upload %s %s at %d of %d bytes", uid[:8],
                 "resumed" if resumed else "created", row["received"], row["size"])
        return {"upload_id": uid, "received": row["received"], "state": row["state"],
                "resumed": resumed}

    # ----- put ------------------------------------------------------------
    def put(self, upload_id, offset, body: bytes) -> dict:
        part = self._part(upload_id)
        if not _is_count(offset):
            raise BatchError("offset must be a whole number of bytes")
        with self._lock(upload_id):
            # Checked BEFORE the offset, so a client already resending from
            # where the bytes really end is accepted straight away.
            up = self._sync_to_part(upload_id, part)
            if up["state"] != "receiving":
                raise UploadConflict("the upload is no longer receiving",
                                     received=up["received"], state=up["state"])
            if offset != up["received"]:
                raise UploadConflict("offset does not match what the server holds",
                                     received=up["received"], state=up["state"])
            if not body:
                raise BatchError("empty chunk")
            if offset + len(body) > up["size"]:
                raise BatchError("chunk runs past the declared file size")
            self.guard_disk(len(body))
            try:
                new = not part.exists()
                with open(part, "wb" if new else "r+b") as f:
                    if os.fstat(f.fileno()).st_nlink > 1:
                        # The part shares its inode with another name -- a
                        # staged drawing's input.pdf left by a completion
                        # that could not clean up. Writing would change
                        # that drawing's bytes under its sha256.
                        log.warning("upload %s not stored: part is linked elsewhere",
                                    upload_id[:8])
                        raise StorageFault()
                    f.seek(offset)
                    f.write(body)
                    # Bytes past this chunk can only be a write whose offset
                    # move never committed (a crash between fsync and index);
                    # they are not the file's.
                    f.truncate()
                    f.flush()
                    os.fsync(f.fileno())
                if new:
                    _fsync_dir(part.parent)
            except OSError as e:
                raise self._storage_error(e, upload_id) from None
            try:
                received = self.repo.upload_received(upload_id, offset, offset + len(body))
            except UnknownUpload:
                part.unlink(missing_ok=True)       # discarded while we wrote
                raise
        return {"received": received}

    # ----- complete -------------------------------------------------------
    def complete(self, upload_id) -> dict:
        part = self._part(upload_id)
        with self._lock(upload_id):
            up = self.repo.get_upload(upload_id)
            if up["state"] == "complete":
                return self._completed(upload_id)
            if up["state"] != "receiving" or up["received"] != up["size"]:
                raise UploadConflict("the upload is not complete",
                                     received=up["received"], state=up["state"])
            # The index is the client's count; the part file is what the
            # server holds. Only bytes on disk count.
            held = part.stat().st_size if part.exists() else 0
            try:
                if held > up["size"]:
                    # put never writes past the declared size, so these
                    # bytes came from outside this process and none of the
                    # file can be trusted. (Resetting to `size` would answer
                    # 409 at `size` forever.) Start the file again -- as a
                    # NEW inode, never truncated in place: the old one may
                    # also be a staged drawing's input.pdf.
                    part.unlink()
                    held = 0
                if held == 0 and not part.exists():
                    # Recreated empty: a zero-byte upload is then complete
                    # as it stands, and any other resends into it.
                    with open(part, "xb"):
                        pass
                    _fsync_dir(part.parent)
            except OSError as e:
                raise self._storage_error(e, upload_id) from None
            if held != up["size"]:
                # The index counted bytes the file does not hold: the client
                # resends from what is really there.
                self.repo.reset_received(upload_id, held)
                raise UploadConflict("the server holds less of this file than it counted",
                                     received=held, state="receiving")
            self.repo.begin_completion(upload_id)
            created = []
            try:
                docs, ignored = self._stage_files(part, up, created)
                if created:
                    _fsync_dir(self.data.docs)
                ignored = summarise_ignored(ignored)
                # The commit and the unmarking under the batch's lock: a
                # Start or Discard that snapshotted these rows in between
                # would mark their dirs, and this unlink would then strip
                # ITS marker -- after its commit, a crash would leave an
                # unmarked dir with no row, which nothing ever removes.
                # (_unmark never raises, so a committed completion is never
                # rolled back below.)
                with self._batch_lock(up["batch_id"]):
                    rows = self.repo.finish_completion(upload_id, docs, ignored)
                    # Committed: the rows own the dirs now, so the markers go.
                    self._unmark(created)
            except BaseException as e:
                for d in created:
                    self._remove_import_dir(d)
                try:
                    self.repo.abort_completion(upload_id)
                except Exception as abort_error:
                    log.warning("upload %s: completion not rolled back: %s",
                                upload_id[:8], type(abort_error).__name__)
                if isinstance(e, OSError):
                    raise self._storage_error(e, upload_id) from None
                raise
            # The part (whose bytes are hard-linked or copied) is spent.
            part.unlink(missing_ok=True)
        log.info("upload %s complete: %d staged, %d ignored",
                 upload_id[:8], len(rows), ignored["total"])
        return {"upload_id": upload_id, "state": "complete", "documents": rows,
                "ignored": ignored["listed"], "ignored_total": ignored["total"],
                "ignored_by_reason": ignored["by_reason"]}

    def _completed(self, upload_id) -> dict:
        done = self.repo.completed_upload(upload_id)
        u = done["upload"]
        return {"upload_id": upload_id, "state": "complete", "documents": done["documents"],
                "ignored": u["ignored"], "ignored_total": u["ignored_total"],
                "ignored_by_reason": u["ignored_by_reason"]}

    def _stage_files(self, part, up, created):
        """(documents to stage, ignored entries) for one completed upload."""
        if up["kind"] == "zip":
            return self._stage_zip(part, up, created)
        return [self._stage_pdf(part, up, created)], []

    def _stage_zip(self, part, up, created):
        """One staged document per PDF entry, in archive order; every other
        entry listed as ignored. A damaged ENTRY is ignored alone and the
        rest still staged -- the user sees which one and why. Anything else
        (the disk guard, a write fault) raises, and complete() rolls the
        whole upload back for a retry: all of it is staged or none, so a
        retried completion can never stage an entry twice."""
        docs, ignored = [], []
        try:
            zf = zipfile.ZipFile(part)
        except (zipfile.BadZipFile, EOFError, ValueError, NotImplementedError):
            # Not an archive at all (or one whose directory is unreadable):
            # listed under the upload's own name, nothing staged.
            return [], [ignored_entry(up["name"], "unreadable_zip").as_dict()]
        with zf:
            infos = zf.infolist()
            shared = shared_headers(infos)
            part_size = part.stat().st_size
            for info in infos:
                entry = classify(info)
                if entry is None:
                    continue
                if isinstance(entry, Ignored):
                    ignored.append(entry.as_dict())
                    continue
                if (info.header_offset in shared
                        or not 0 <= info.header_offset < part_size):
                    # Never opened, the archive's damage:
                    # - a local header shared with another record (an
                    #   overlap bomb): zipfile's own check would WARN with
                    #   the entry's name, and a warning reaches the log;
                    # - a local header outside the file (a lying end record
                    #   shifts every offset): the seek raises EINVAL, an
                    #   OSError WITH an errno that would read as the disk
                    #   failing -- a 500 on every retry.
                    ignored.append(ignored_entry(info.filename, "unreadable_entry").as_dict())
                    continue
                doc_id, d = self._new_doc_dir(created)
                target = d / "input.pdf"
                try:
                    sha256, size = self._copy_entry(zf, info, target)
                except _EntryUnreadable:
                    # Marker last (_remove_import_dir): should the removal
                    # stop halfway, the dir is still marked and rowless, and
                    # the startup sweep finishes it.
                    created.remove(d)
                    self._remove_import_dir(d)
                    ignored.append(ignored_entry(info.filename, "unreadable_entry").as_dict())
                    continue
                _fsync_dir(d)
                facts = pdf_facts(target)
                docs.append({
                    "id": doc_id, "file_name": entry.file_name,
                    # A ZIP picked from inside a folder upload keeps both.
                    "rel_path": "/".join(p for p in (up["rel_path"], entry.folder) if p),
                    "size": size, "sha256": sha256, "pages": facts.pages,
                    "oversized": facts.oversized, "unreadable": not facts.readable})
        return docs, ignored

    def _copy_entry(self, zf, info, target):
        """Stream one entry to `target`, hashing as it goes. The disk guard
        runs per piece, on the bytes actually decompressed: an entry's
        declared size can lie, and decompression can expand a small archive
        into a full disk. Size and hash are measured, never taken from the
        entry's header."""
        digest = hashlib.sha256()
        size = 0
        try:
            src = zf.open(info)
        except _ENTRY_ERRORS as e:
            raise _EntryUnreadable() from e
        except OSError as e:
            _entry_or_disk(e)
        with src, open(target, "xb") as out:
            while True:
                try:
                    piece = src.read(_COPY_PIECE)
                except _ENTRY_ERRORS as e:
                    raise _EntryUnreadable() from e
                except OSError as e:
                    _entry_or_disk(e)
                if not piece:
                    break
                self.guard_disk(len(piece))
                out.write(piece)
                digest.update(piece)
                size += len(piece)
            out.flush()
            os.fsync(out.fileno())
        return digest.hexdigest(), size

    @staticmethod
    def _remove_import_dir(d) -> None:
        """Undo a docs/<id>/ whose rows never committed: its contents first,
        the .importing marker LAST, then the dir. A removal that stops
        halfway leaves a dir that is still marked, which the startup sweep
        finishes -- never an unmarked dir with no row, which nothing would
        ever remove."""
        try:
            for child in d.iterdir():
                if child.name == IMPORTING_MARKER:
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            (d / IMPORTING_MARKER).unlink(missing_ok=True)
            d.rmdir()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("import dir %s not removed: %s", d.name[:8], type(e).__name__)

    def _sync_to_part(self, upload_id, part) -> dict:
        """The upload's row, its offset first brought down to the bytes the
        part file really holds (a missing part holds 0). Only bytes on disk
        count: a part cut short outside this process would otherwise take
        the next chunk past its end, and the gap would read back as zeros
        in the staged drawing. Caller holds the upload's lock."""
        up = self.repo.get_upload(upload_id)
        if up["state"] == "receiving":
            held = part.stat().st_size if part.exists() else 0
            if held < up["received"]:
                self.repo.reset_received(upload_id, held)
                up = self.repo.get_upload(upload_id)
        return up

    def _new_doc_dir(self, created):
        doc_id = uuid.uuid4().hex
        d = self.data.doc_dir(doc_id)
        d.mkdir()
        created.append(d)
        # FIRST, before any content: until the rows commit this dir belongs
        # to no row, and only a marked dir is ever swept.
        (d / IMPORTING_MARKER).write_bytes(b"")
        return doc_id, d

    def _stage_pdf(self, part, up, created) -> dict:
        doc_id, d = self._new_doc_dir(created)
        target = d / "input.pdf"
        try:
            # A hard link: no second copy of a large file, and the part
            # stays whole until the rows commit, so a failed completion is
            # simply retried.
            os.link(part, target)
        except OSError as e:
            if e.errno not in _NO_LINK:
                raise
            # A filesystem without hard links (some network mounts).
            self.guard_disk(up["size"])
            shutil.copyfile(part, target)
            _fsync_file(target)
        _fsync_dir(d)
        facts = pdf_facts(target)
        # Size and hash are measured from the bytes staged, not taken from
        # the client's declaration (complete() checked they agree in length).
        return {"id": doc_id, "file_name": up["name"], "rel_path": up["rel_path"],
                "size": target.stat().st_size, "sha256": sha256_file(target),
                "pages": facts.pages, "oversized": facts.oversized,
                "unreadable": not facts.readable}

    # ----- delete ---------------------------------------------------------
    def delete(self, upload_id) -> None:
        """An unfinished upload the user removed. Idempotent; a complete one
        is refused by the repo (its drawings are removed one by one)."""
        part = self._part(upload_id)
        with self._lock(upload_id):
            self.repo.delete_upload(upload_id)
            part.unlink(missing_ok=True)

    # ----- start and discard: rows go first, files follow -------------------
    def _mark(self, batch_id, doc_ids) -> list:
        """Mark each drawing dir BEFORE a transaction that may remove its
        row: a crash after the commit leaves a marked dir with no row, which
        the startup sweep removes; a crash before it leaves a marked dir
        with its row, which the sweep only unmarks. All or none: a marker
        that cannot be written unmarks the rest and refuses the step."""
        marked = []
        try:
            for doc_id in doc_ids:
                d = self.data.doc_dir(doc_id)
                if not d.is_dir():
                    continue
                try:
                    (d / IMPORTING_MARKER).write_bytes(b"")
                except FileNotFoundError:
                    # Removed since is_dir() (DELETE /api/documents takes the
                    # row, then the dir): the import changed, the disk did
                    # not fail. Skipped; the repo's snapshot check refuses
                    # the step with a 409, not a 500.
                    continue
                marked.append(d)
        except OSError as e:
            self._unmark(marked)
            log.warning("batch %s: dirs not marked: %s", batch_id[:8], type(e).__name__)
            raise DiskFull() if e.errno == errno.ENOSPC else StorageFault() from None
        return marked

    @staticmethod
    def _unmark(dirs) -> None:
        """Take markers off dirs whose rows stand. Never raises: a marker
        left on a dir WITH a row is harmless (the startup sweep only strips
        it), whereas raising here could roll back work that committed."""
        for d in dirs:
            try:
                (d / IMPORTING_MARKER).unlink(missing_ok=True)
            except OSError as e:
                log.warning("import dir %s not unmarked: %s", d.name[:8], type(e).__name__)

    def _guarded(self, batch_id, step):
        """Snapshot the draft's drawings, mark their dirs, then run the repo
        `step` with that snapshot as `expected_ids` -- all under the batch's
        lock, so no completion can commit or unmark in between. The repo
        refuses (409, "the import changed") a draft that no longer matches
        the snapshot: a row staged since has an UNMARKED dir that a crash
        after the commit would leak. A refusal is not retried here -- the
        user reloads and decides again. On any refusal or failure every
        marker comes off and nothing has changed. Returns (repo result,
        dirs marked).

        An unknown or started batch is handed to the repo with nothing
        marked: its refusal (or a Discard's idempotent None) is certain, and
        a started batch's dirs are the worker's -- no marker belongs there.
        Read under the batch's lock, so no Start can slip in between; a
        started batch never becomes a draft again."""
        with self._batch_lock(batch_id):
            try:
                draft = self.repo.get_batch(batch_id)["draft"]
            except UnknownBatch:
                draft = False
            if not draft:
                return step(batch_id, expected_ids=[]), []
            snapshot = self.repo.batch_document_ids(batch_id)
            marked = self._mark(batch_id, snapshot)
            try:
                return step(batch_id, expected_ids=snapshot), marked
            except BaseException:
                self._unmark(marked)        # refused or failed: every row still stands
                raise

    def start_batch(self, batch_id) -> dict:
        """Repo.start_batch, with the dropped drawings' dirs removed after
        the commit (marker last, so a removal that stops halfway is still
        swept). The queued drawings' dirs are unmarked. Returns the repo's
        {queued, dropped}."""
        out, marked = self._guarded(batch_id, self.repo.start_batch)
        dropped = set(out["dropped"])
        self._unmark(d for d in marked if d.name not in dropped)
        for doc_id in out["dropped"]:
            self._remove_import_dir(self.data.doc_dir(doc_id))
        return out

    def discard_batch(self, batch_id) -> None:
        """Repo.delete_batch, then the files: the staged drawings' dirs and
        the part files of unfinished uploads. Idempotent for an unknown
        batch; a started one is refused (409) with its markers taken off."""
        out, _ = self._guarded(batch_id, self.repo.delete_batch)
        # The batch is gone (or never was): forget its lock, or one would
        # outlive every discarded batch for the life of the process. Safe
        # even if another thread still holds or awaits the old lock object:
        # any step on a vanished batch finds it unknown and marks nothing,
        # and a completion into it finds its upload row gone.
        with self._locks_guard:
            self._batch_locks.pop(batch_id, None)
        if out is None:
            return
        for uid in out["uploads"]:
            # Under the upload's lock (and no longer the batch's -- see the
            # lock order in __init__): a chunk being written finishes first,
            # then finds its row gone and removes its part itself.
            with self._lock(uid):
                try:
                    self.data.part_path(uid).unlink(missing_ok=True)
                except OSError as e:
                    # Committed already; the stray-part sweep finishes it.
                    log.warning("upload %s: part not removed: %s", uid[:8], type(e).__name__)
        for doc_id in out["documents"]:
            self._remove_import_dir(self.data.doc_dir(doc_id))
        log.info("batch %s discarded: %d staged, %d uploads",
                 batch_id[:8], len(out["documents"]), len(out["uploads"]))

    # ----- recovery and housekeeping ----------------------------------------
    @staticmethod
    def _housekeeping(what, step):
        """Run one recovery step; a fault is logged by TYPE (an OSError's
        text names a path, CLAUDE.md §1) and answered None, so one failing
        step never skips the others."""
        try:
            return step()
        except Exception as e:
            log.warning("%s failed: %s", what, type(e).__name__)
            return None

    def recover(self) -> dict:
        """ONLY from Worker.start(), right after this process took the
        single-worker lock (Runtime wires it as the worker's `startup`
        hook): the one moment this process is known to be the data dir's
        only worker, before the app serves a request. Never call it while
        requests run -- an in-flight completion's dirs and `completing` row
        look exactly like a crash's leftovers. Counts only; None for a step
        that failed."""
        out = {"completions_reset": self._housekeeping(
                   "resetting interrupted completions", self.repo.reset_completing),
               "imports_swept": self._housekeeping(
                   "sweeping unfinished imports", self.sweep_unfinished_imports)}
        stale = self._housekeeping("stale upload cleanup", self.cleanup_stale)
        out.update(stale or dict.fromkeys(("stale_uploads", "orphan_parts", "empty_drafts")))
        if any(out.values()):
            log.info("startup: %s", ", ".join(f"{k} {v}" for k, v in out.items()))
        return out

    def sweep_unfinished_imports(self) -> int:
        """Remove the docs/<id>/ dirs an index transaction never came to own:
        marker present AND no row (a crash before a completion's commit, or
        after a Start/Discard commit removed the row). A marked dir that HAS
        a row -- in any state: a queued or extracting drawing's dir may carry
        a marker after a crash in Start -- only loses its marker. A dir
        without a marker is never touched whatever the index says, so a lost
        or replaced index can never make this delete real drawings; those
        rowless ones are counted in the log, never named.

        Removal is marker LAST (_remove_import_dir): a sweep that stops
        halfway leaves the dir marked for the next one. Startup only (see
        recover). Returns the number of dirs removed."""
        swept = unmarked = 0
        for d in self.data.docs.iterdir():
            # Only a real docs/<32 hex>/ directory is ours to judge: a
            # symlink's target is not, whatever it holds.
            if not DOC_ID_RE.fullmatch(d.name) or d.is_symlink() or not d.is_dir():
                continue
            owned = self.repo.has_document(d.name)
            if not (d / IMPORTING_MARKER).exists():
                unmarked += not owned
                continue
            if owned:
                self._unmark([d])
                continue
            self._remove_import_dir(d)
            if not d.exists():
                swept += 1
        if unmarked:
            log.warning("startup sweep: %d unmarked dir(s) without a row left alone", unmarked)
        return swept

    def cleanup_stale(self, now=None) -> dict:
        """Unfinished uploads untouched for STALE_AFTER (row and part), part
        files no unfinished upload owns, and drafts left empty as long.

        Safe while requests run (POST /api/batches calls it), because every
        removal takes the lock the protocol's own steps take, in their order
        (upload lock -> batch lock -> repo lock), and re-checks inside its
        transaction:
        - a stale upload is deleted under its upload lock, and only if it is
          still unfinished and still old -- a chunk or a resume since (both
          bump updated_at) keeps it;
        - a part file is only ever UNLINKED, never written or truncated: after
          a crash between a completion's commit and its unlink the part
          shares its inode with a staged drawing's input file. It is stray
          only when no row owns it or its upload is complete; a part of an
          upload still receiving or completing is never touched. Parts are
          listed BEFORE the owners are read, and a part is created only
          after its row commits, so a listed part's owner is always seen;
        - an empty draft is removed under its batch lock (Start, Discard and
          a completion's commit hold it), in one transaction that re-checks
          it is still empty, with its complete upload rows.
        It never sweeps docs/ -- that is startup-only (recover)."""
        now = now or datetime.now(timezone.utc)
        cutoff = (now - STALE_AFTER).isoformat(timespec="microseconds")
        uploads = 0
        for uid in self.repo.stale_uploads(cutoff):
            with self._lock(uid):
                if self.repo.delete_upload(uid, older_than=cutoff) is None:
                    continue
                uploads += 1
                try:
                    self.data.part_path(uid).unlink(missing_ok=True)
                except OSError as e:
                    # The row is gone; the stray-part pass below (or the
                    # next cleanup) finishes it.
                    log.warning("upload %s: part not removed: %s", uid[:8], type(e).__name__)
        parts = self._remove_stray_parts()
        drafts = 0
        for batch_id in self.repo.empty_drafts(cutoff):
            with self._batch_lock(batch_id):
                gone = self.repo.delete_empty_draft(batch_id, cutoff)
            if gone:
                drafts += 1
                # Like discard_batch: no lock outlives its batch.
                with self._locks_guard:
                    self._batch_locks.pop(batch_id, None)
        out = {"stale_uploads": uploads, "orphan_parts": parts, "empty_drafts": drafts}
        if any(out.values()):
            log.info("stale cleanup: %s", ", ".join(f"{k} {v}" for k, v in out.items()))
        return out

    def _remove_stray_parts(self) -> int:
        found = [p for p in self.data.uploads.iterdir()
                 if p.suffix == ".part" and DOC_ID_RE.fullmatch(p.stem)]
        live = self.repo.open_upload_ids()
        removed = 0
        for p in found:
            uid = p.stem
            if uid in live or (p.is_dir() and not p.is_symlink()):
                continue
            with self._lock(uid):
                # Re-read under the upload's lock: whatever `live` said, a
                # part whose upload is unfinished right now is never touched.
                # (Neither a complete nor a deleted upload becomes unfinished
                # again, so this is defence in depth, not a known race.)
                try:
                    if self.repo.get_upload(uid)["state"] != "complete":
                        continue
                except UnknownUpload:
                    pass
                try:
                    p.unlink()              # the NAME only -- never open, never truncate
                except FileNotFoundError:
                    continue                # its own step removed it meanwhile
                except OSError as e:
                    log.warning("upload %s: stray part not removed: %s",
                                uid[:8], type(e).__name__)
                    continue
                removed += 1
        return removed
