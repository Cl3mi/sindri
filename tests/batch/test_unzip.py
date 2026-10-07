"""ZIP entry classification (batch design §2, decision 22): PDFs only,
path-safe. Pure: built on ZipInfo objects, no archive needed (an encrypted
entry's flag cannot even be written by zipfile)."""
import stat
import zipfile

import pytest

from app.batch.unzip import Ignored, PdfEntry, classify


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
    ("a\x01b.pdf", {}, "unsafe_path"),
    ("exe.\u202efdp.pdf", {}, "unsafe_path"),
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
