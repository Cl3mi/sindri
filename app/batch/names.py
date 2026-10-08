"""Windows-safe file and folder names for the mass-export ZIP (batch design
§4). app/batch/paths.py's safe_rel_parts already keeps a folder name free of
control, format and line-break characters, and its own docstring names three
traps it deliberately leaves open -- no client-supplied name was ever
written to a real filesystem path until this export's ZIP. This module
closes them for every component the export itself builds."""
import re
import unicodedata

# CON, PRN, AUX, NUL, COM1-9, LPT1-9 -- reserved with or without an
# extension (Windows treats "NUL.txt" the same as "NUL").
_RESERVED = re.compile(r"(?i)\A(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|\Z)")
# ':' starts an NTFS alternate data stream; '<>"|?*' and the two path
# separators cannot appear in a single Windows path component at all.
_WINDOWS_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Look-alike separators Unicode does NOT fold via NFKC (the fullwidth
# solidus U+FF0F does fold, to ordinary '/', and is caught by _WINDOWS_UNSAFE
# after normalisation below -- these do not, and must be named explicitly).
_LOOKALIKE_SEPARATORS = str.maketrans({"∕": "/", "⁄": "/"})


def windows_safe_component(name) -> str:
    """One path component (a batch name, a folder from rel_path, a file
    stem) made safe to write as-is on Windows. Rewrites rather than refuses:
    a mass export must account for every drawing it was asked for (its
    manifest), and refusing one because its title block happens to collide
    with a reserved device name would be a worse failure than suffixing it.

    Order: look-alike separators first (so DIVISION SLASH becomes an
    ordinary '/' before anything else looks at it), then NFKC (folds the
    fullwidth solidus and fullwidth dot to their ASCII forms), then the
    unsafe-character sweep, then trailing dot/space stripping -- marked with
    a trailing '_' when it actually removed something, so "trailing." and
    "trailing" can never collide silently inside one export -- then the
    reserved-name check."""
    s = (name or "").translate(_LOOKALIKE_SEPARATORS)
    s = unicodedata.normalize("NFKC", s)
    s = _WINDOWS_UNSAFE.sub("_", s)
    stripped = s.rstrip(" .")
    if stripped != s and stripped:
        s = stripped + "_"
    else:
        s = stripped
    if not s:
        s = "_"
    if _RESERVED.match(s):
        s = s + "_"
    return s


def unique_export_path(used: set, folder_parts: list, stem: str, ext: str) -> str:
    """`folder/stem.ext`, suffixed " (2)", " (3)", ... the first time that
    exact path has already been used in THIS export. `used` is mutated: the
    caller passes the same set across every document and both of its own
    files, so a drawing's xlsx and its own ballooned PDF, and two different
    drawings that sanitise to the same name, never collide."""
    folder = "/".join(p for p in folder_parts if p)
    base = f"{folder}/{stem}" if folder else stem
    candidate = f"{base}{ext}"
    n = 2
    while candidate in used:
        candidate = f"{base} ({n}){ext}"
        n += 1
    used.add(candidate)
    return candidate
