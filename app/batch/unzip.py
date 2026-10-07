"""ZIP entries for an import (batch design §2, decision 22): PDFs only,
path-safe, and only what can be read with bounded memory.

An entry is never written to a path built from its name -- every PDF is
copied to docs/<new id>/input.pdf (uploads.py) -- so a hostile name cannot
escape the data dir by construction. Names are still judged, because the
folder is shown to the user and becomes a folder in the mass-export ZIP
(design §4): an absolute, climbing or control-character path, a symlink, an
encrypted entry, an OS litter file, an entry compressed with anything but
stored/deflate, and every non-PDF are listed as ignored, with the reason,
and never staged. A ZIP inside the ZIP is a non-PDF: it is listed, never
opened, so nesting cannot multiply what one upload unpacks.

An ignored entry's name is listed through paths.shown_name (refused
characters replaced, length bounded), and the list itself is capped
(summarise_ignored): an archive is client input and its listing is shown.

Pure: judges ZipInfo objects and reads nothing. Never logs -- an entry name
is client data (CLAUDE.md §1)."""
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import Optional, Union

from app.batch.paths import safe_rel_parts, shown_name

# Litter that operating systems add to archives; listed, never staged.
_SYSTEM_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
# The only methods whose reads zipfile bounds: deflate decompresses with a
# max_length, stored copies. bzip2, LZMA and zstd decompress a whole
# compressed read with NO output bound -- 536 bytes of bzip2 became +1 GiB of
# memory in one read, before the per-piece disk guard ever saw a byte.
_READABLE_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
# How many ignored entries a completion lists; the total and the counts by
# reason always cover all of them.
LISTED_IGNORED = 1000


@dataclass(frozen=True)
class PdfEntry:
    info: zipfile.ZipInfo
    file_name: str        # NFC, like an uploaded file's name
    folder: str           # posix, '' at the archive's root


@dataclass(frozen=True)
class Ignored:
    name: str             # as shown to the user (paths.shown_name)
    reason: str           # unsafe_path | symlink | system_file | not_pdf |
                          # encrypted_entry | unsupported_compression |
                          # unreadable_entry | unreadable_zip

    def as_dict(self) -> dict:
        return {"name": self.name, "reason": self.reason}


def ignored(name: str, reason: str) -> Ignored:
    """The one way to list a name: always through shown_name."""
    return Ignored(shown_name(name), reason)


def classify(info: zipfile.ZipInfo) -> Optional[Union[PdfEntry, Ignored]]:
    """None for a directory entry: nothing to stage and nothing to list."""
    name = info.filename
    # zipfile only knows `/`; a Windows tool may end a folder entry with a
    # backslash, which on a POSIX server zipfile leaves as it is. Only an
    # EMPTY one is a folder: an entry with bytes is a file, and is listed.
    if info.is_dir() or (name.endswith("\\") and info.file_size == 0):
        return None
    if stat.S_ISLNK(info.external_attr >> 16):
        return ignored(name, "symlink")
    parts = safe_rel_parts(name)
    if not parts:
        return ignored(name, "unsafe_path")
    base = parts[-1]
    if parts[0] == "__MACOSX" or base.startswith("._") or base.lower() in _SYSTEM_NAMES:
        return ignored(name, "system_file")
    if not base.lower().endswith(".pdf"):
        return ignored(name, "not_pdf")
    if info.flag_bits & 0x1:
        return ignored(name, "encrypted_entry")
    if info.compress_type not in _READABLE_METHODS:
        return ignored(name, "unsupported_compression")
    return PdfEntry(info, base, "/".join(parts[:-1]))


def shared_headers(infos) -> set:
    """header_offsets that more than one central-directory record points
    at: the shape of an overlap zip bomb. zipfile refuses one such entry and
    WARNS on another -- with the entry's name in the text, which reaches
    stderr, i.e. the server log -- so these are never opened at all."""
    counts = Counter(i.header_offset for i in infos)
    return {off for off, n in counts.items() if n > 1}


def summarise_ignored(items) -> dict:
    """What is stored and answered for a completion's ignored entries: the
    first LISTED_IGNORED, plus the total and the counts by reason over ALL
    of them -- an archive of a million non-PDFs must not become a
    million-row reply, and the user still learns how many and why."""
    return {"listed": list(items[:LISTED_IGNORED]), "total": len(items),
            "by_reason": dict(Counter(i["reason"] for i in items))}
