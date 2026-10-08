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

# Classic Windows MAX_PATH is 260 characters for the WHOLE path, and one
# export path stacks a batch name, an upload's rel_path folders, a file
# stem, a dedupe suffix (" (99)"), the ballooned sibling's own "_ballooned"
# and an extension -- all sharing that one budget. Capping any ONE component
# at 100 keeps a path of several components, each near the cap, well inside
# it, without needing to track the running total across the whole call.
_MAX_COMPONENT_LEN = 100


def _strip_trailing_dot_or_space(s: str) -> str:
    """rstrip(" .")'d, with a trailing '_' appended when that actually
    removed something -- so a name that only differs from a sibling by a
    trailing dot or space (which Windows itself would silently strip) can
    never collide with it once both are written as real path components."""
    stripped = s.rstrip(" .")
    if stripped != s and stripped:
        return stripped + "_"
    return stripped


def windows_safe_component(name) -> str:
    """One path component (a batch name, a folder from rel_path, a file
    stem) made safe to write as-is on Windows. Rewrites rather than refuses:
    a mass export must account for every drawing it was asked for (its
    manifest), and refusing one because its title block happens to collide
    with a reserved device name would be a worse failure than suffixing it.

    Order: look-alike separators first (so DIVISION SLASH becomes an
    ordinary '/' before anything else looks at it), then NFKC (folds the
    fullwidth solidus and fullwidth dot to their ASCII forms), then the
    unsafe-character sweep, then trailing dot/space stripping, then the
    length cap -- re-stripping whatever trailing dot/space the cut itself
    exposes, so a 300-character name whose 100th character happens to be a
    space does not come out looking like a DIFFERENT, shorter, untruncated
    name -- then the reserved-name check, which runs on this final string so
    a cut can never expose a bare device name either."""
    s = (name or "").translate(_LOOKALIKE_SEPARATORS)
    s = unicodedata.normalize("NFKC", s)
    s = _WINDOWS_UNSAFE.sub("_", s)
    s = _strip_trailing_dot_or_space(s)
    if not s:
        s = "_"
    if len(s) > _MAX_COMPONENT_LEN:
        s = _strip_trailing_dot_or_space(s[:_MAX_COMPONENT_LEN])
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
    drawings that sanitise to the same name, never collide.

    `used` holds CASEFOLDED candidates: Windows file systems are
    case-insensitive, so "Part.xlsx" and "part.xlsx" are the same path on
    disk and the second one written would silently overwrite the first
    inside the extracted ZIP. The candidate returned to the caller keeps
    whatever case it was asked for -- only the collision check folds it."""
    folder = "/".join(p for p in folder_parts if p)
    base = f"{folder}/{stem}" if folder else stem
    candidate = f"{base}{ext}"
    n = 2
    while candidate.casefold() in used:
        candidate = f"{base} ({n}){ext}"
        n += 1
    used.add(candidate.casefold())
    return candidate
