"""ZIP entry classification (batch design §2, decision 22): PDFs only,
path-safe. Pure: built on ZipInfo objects, no archive needed (an encrypted
entry's flag cannot even be written by zipfile)."""
import stat
import zipfile

import pytest

from app.batch.paths import shown_name
from app.batch.unzip import Ignored, PdfEntry, classify, summarise_ignored


def test_the_ignored_list_is_capped_but_counted_in_full():
    """An archive of a million non-PDFs must not become a million-row JSON
    reply. The first LISTED_IGNORED are listed; the total and the counts by
    reason cover every one, so the user still learns how many and why."""
    items = [{"name": f"n{i}.txt", "reason": "not_pdf"} for i in range(1002)]
    items.append({"name": "x", "reason": "unsafe_path"})
    s = summarise_ignored(items)
    assert s["listed"] == items[:1000]
    assert (s["total"], s["by_reason"]) == (1003, {"not_pdf": 1002, "unsafe_path": 1})


def info(name, *, symlink=False, encrypted=False):
    i = zipfile.ZipInfo(name)
    if symlink:
        i.external_attr = (stat.S_IFLNK | 0o777) << 16
    if encrypted:
        i.flag_bits |= 0x1
    return i


@pytest.mark.parametrize("name,file_name,folder", [
    ("w1.pdf", "w1.pdf", ""),
    ("Teile/Welle/w1.PDF", "w1.PDF", "Teile/Welle"),
    ("Teile\\Welle\\w1.pdf", "w1.pdf", "Teile/Welle"),
])
def test_a_pdf_entry_keeps_its_folder(name, file_name, folder):
    e = classify(info(name))
    assert isinstance(e, PdfEntry) and (e.file_name, e.folder) == (file_name, folder)


def test_a_macos_nfd_name_is_staged_nfc():
    """Like a folder upload's names (paths.safe_rel_parts): one name, one
    spelling, whichever OS packed the archive."""
    e = classify(info("Bru\u0308cke/Ste\u0301g.pdf"))
    assert (e.file_name, e.folder) == ("St\u00e9g.pdf", "Br\u00fccke")


@pytest.mark.parametrize("name,kw,reason", [
    ("../../evil.pdf", {}, "unsafe_path"),
    ("/etc/abs.pdf", {}, "unsafe_path"),
    ("C:\\win.pdf", {}, "unsafe_path"),
    (".", {}, "unsafe_path"),
    ("link.pdf", {"symlink": True}, "symlink"),
    ("notes.txt", {}, "not_pdf"),
    ("inner.zip", {}, "not_pdf"),
    ("Teile/inner.pdf.zip", {}, "not_pdf"),
    ("__MACOSX/Teile/._w1.pdf", {}, "system_file"),
    ("Teile/._w1.pdf", {}, "system_file"),
    ("Teile/Thumbs.db", {}, "system_file"),
    ("secret.pdf", {"encrypted": True}, "encrypted_entry"),
])
def test_everything_else_is_listed_as_ignored_with_its_reason(name, kw, reason):
    assert classify(info(name, **kw)) == Ignored(name, reason)


@pytest.mark.parametrize("name", ["Teile/", "Teile\\Welle\\"])
def test_a_directory_entry_is_neither_staged_nor_listed(name):
    """Windows tools may end a folder entry with a backslash; on a POSIX
    server zipfile leaves it as is, and it is still a folder, not a file
    to list as 'not a PDF'."""
    assert classify(info(name)) is None


def test_a_backslash_entry_that_holds_bytes_is_not_a_folder():
    """Only an EMPTY entry ending in a backslash is a folder: one with
    content is a file, and silently dropping it would hide it from the
    user. It is listed."""
    i = info("Teile\\Welle\\")
    i.file_size = 10
    assert classify(i) == Ignored("Teile\\Welle\\", "not_pdf")


@pytest.mark.parametrize("method", [
    zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA, getattr(zipfile, "ZIP_ZSTANDARD", 93)],
    ids=["bzip2", "lzma", "zstd"])
def test_only_stored_and_deflated_entries_are_ever_read(method):
    """zipfile decompresses bzip2, LZMA and zstd reads with no output bound:
    a few hundred bytes become a GiB in memory before the per-piece disk
    guard sees a byte. Only stored and deflate (whose reads ARE bounded) are
    opened; anything else is listed, never decompressed."""
    i = info("Teile/w1.pdf")
    i.compress_type = method
    assert classify(i) == Ignored("Teile/w1.pdf", "unsupported_compression")


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED],
                         ids=["stored", "deflated"])
def test_stored_and_deflated_pdfs_are_staged(method):
    i = info("w1.pdf")
    i.compress_type = method
    assert isinstance(classify(i), PdfEntry)


@pytest.mark.parametrize("name,shown", [
    ("a\x01b.pdf", "a�b.pdf"),
    ("exe.‮fdp.pdf", "exe.�fdp.pdf"),
    ("x\n../evil.pdf", "x�../evil.pdf"),
])
def test_an_ignored_name_is_listed_with_its_refused_characters_replaced(name, shown):
    """The name is shown to the user: a bidi override would make it lie
    about its extension, a newline would forge a line. Each such character
    is shown as U+FFFD, so the user still sees that something was there."""
    assert classify(info(name)) == Ignored(shown, "unsafe_path")


def test_an_ignored_name_is_listed_nfc_and_bounded():
    long = "Brücke/" + "x" * 1000 + ".txt"
    got = classify(info(long))
    assert got.reason == "not_pdf"
    assert got.name.startswith("Brücke/") and got.name.endswith("…")
    assert len(got.name) == 255


def test_shown_name():
    assert shown_name("ok.pdf") == "ok.pdf"
    assert shown_name("a\x00b") == "a�b"
    assert shown_name("y" * 255) == "y" * 255
    assert shown_name("y" * 256) == "y" * 254 + "…"
