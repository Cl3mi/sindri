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
import os
import re
import shutil
import threading
import unicodedata
import uuid
import zipfile
import zlib
from typing import Optional

from app.batch.paths import DOC_ID_RE, has_refused_chars, safe_rel_parts
from app.batch.pdf_facts import pdf_facts
from app.batch.repo import BatchError, UnknownBatch, UnknownUpload, UploadConflict
from app.batch.unzip import Ignored, classify

log = logging.getLogger(__name__)

MIB = 1024 * 1024
# The most one PUT may carry. It bounds the memory of ONE REQUEST, not the
# size of a file: a file of any size arrives in as many chunks as it needs
# (decision 29 -- no upload limits). The browser sends 8 MiB (upload.js).
MAX_CHUNK = 64 * MIB
# \A...\Z and fullmatch, like paths.DOC_ID_RE: `$` also matches before a
# trailing newline, and a fingerprint is part of the resume key.
FINGERPRINT_RE = re.compile(r"\A[0-9a-f]{64}\Z")
# Marks a docs/<id>/ written by a completion whose index rows have not
# committed yet. Only a marked dir with no row is ever swept at startup, so
# a lost or replaced index can never make the sweep delete real drawings.
IMPORTING_MARKER = ".importing"
_COPY_PIECE = MIB
# What reading a damaged entry can raise: a CRC mismatch, a broken deflate
# stream, a truncated archive, an unsupported compression method, an
# encrypted entry unzip.py did not see flagged. Each makes that ONE entry
# ignored, never the whole import. OSError is deliberately absent: a disk
# that fails under the part file is the server's fault, not the entry's.
_ENTRY_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError,
                 RuntimeError, ValueError)
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

    def _lock(self, upload_id) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(upload_id, threading.Lock())

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
                rows = self.repo.finish_completion(upload_id, docs, ignored)
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
            # Committed: the rows own the dirs now, so the markers go, and
            # the part (whose bytes are hard-linked or copied) is spent.
            for d in created:
                (d / IMPORTING_MARKER).unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        log.info("upload %s complete: %d staged, %d ignored",
                 upload_id[:8], len(rows), len(ignored))
        return {"upload_id": upload_id, "state": "complete", "documents": rows,
                "ignored": ignored}

    def _completed(self, upload_id) -> dict:
        done = self.repo.completed_upload(upload_id)
        return {"upload_id": upload_id, "state": "complete", "documents": done["documents"],
                "ignored": done["upload"]["ignored"]}

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
            return [], [Ignored(up["name"], "unreadable_zip").as_dict()]
        with zf:
            for info in zf.infolist():
                entry = classify(info)
                if entry is None:
                    continue
                if isinstance(entry, Ignored):
                    ignored.append(entry.as_dict())
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
                    ignored.append(Ignored(info.filename, "unreadable_entry").as_dict())
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
        with src, open(target, "xb") as out:
            while True:
                try:
                    piece = src.read(_COPY_PIECE)
                except _ENTRY_ERRORS as e:
                    raise _EntryUnreadable() from e
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
