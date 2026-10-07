"""ZIP entries for an import (batch design §2, decision 22): PDFs only,
path-safe.

An entry is never written to a path built from its name -- every PDF is
copied to docs/<new id>/input.pdf (uploads.py) -- so a hostile name cannot
escape the data dir by construction. Names are still judged, because the
folder is shown to the user and becomes a folder in the mass-export ZIP
(design §4): an absolute, climbing or control-character path, a symlink, an
encrypted entry, an OS litter file and every non-PDF are listed as ignored,
with the reason, and never staged. A ZIP inside the ZIP is a non-PDF: it is
listed, never opened, so nesting cannot multiply what one upload unpacks.

Pure: judges a ZipInfo and reads nothing. Never logs -- an entry name is
client data (CLAUDE.md §1)."""
import stat
import zipfile
from dataclasses import dataclass
from typing import Optional, Union

from app.batch.paths import safe_rel_parts

# Litter that operating systems add to archives; listed, never staged.
_SYSTEM_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


@dataclass(frozen=True)
class PdfEntry:
    info: zipfile.ZipInfo
    file_name: str        # NFC, like an uploaded file's name
    folder: str           # posix, '' at the archive's root


@dataclass(frozen=True)
class Ignored:
    name: str             # the entry's own name, shown to the user
    reason: str           # unsafe_path | symlink | system_file | not_pdf |
                          # encrypted_entry | unreadable_entry | unreadable_zip

    def as_dict(self) -> dict:
        return {"name": self.name, "reason": self.reason}


def classify(info: zipfile.ZipInfo) -> Optional[Union[PdfEntry, Ignored]]:
    """None for a directory entry: nothing to stage and nothing to list."""
    name = info.filename
    # zipfile only knows `/`; a Windows tool may end a folder entry with a
    # backslash, which on a POSIX server zipfile leaves as it is.
    if info.is_dir() or name.endswith("\\"):
        return None
    if stat.S_ISLNK(info.external_attr >> 16):
        return Ignored(name, "symlink")
    parts = safe_rel_parts(name)
    if not parts:
        return Ignored(name, "unsafe_path")
    base = parts[-1]
    if parts[0] == "__MACOSX" or base.startswith("._") or base.lower() in _SYSTEM_NAMES:
        return Ignored(name, "system_file")
    if not base.lower().endswith(".pdf"):
        return Ignored(name, "not_pdf")
    if info.flag_bits & 0x1:
        return Ignored(name, "encrypted_entry")
    return PdfEntry(info, base, "/".join(parts[:-1]))
