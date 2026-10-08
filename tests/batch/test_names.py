"""Windows-safe file/folder names for the mass-export ZIP (batch design §4).
paths.safe_rel_parts already keeps a folder name free of control, format and
line-break characters (B2); its own docstring lists three Windows traps it
deliberately leaves unhandled, because nothing wrote a client-supplied name
to a real filesystem path until this export does. This module closes them
for every component the export itself builds -- a batch name, a folder from
an upload's rel_path, a file stem -- never for a path that is merely stored
or shown elsewhere."""
import pytest

from app.batch.names import unique_export_path, windows_safe_component


@pytest.mark.parametrize("name,expected", [
    ("Flansch", "Flansch"),
    ("a:b", "a_b"),
    ("a/b\\c", "a_b_c"),
    ("a<b>c", "a_b_c"),
    ('a"b|c?d*e', "a_b_c_d_e"),
    ("trailing.", "trailing_"),
    ("trailing ", "trailing_"),
    ("", "_"),
])
def test_windows_safe_component_replaces_unsafe_characters(name, expected):
    assert windows_safe_component(name) == expected


@pytest.mark.parametrize("name", ["CON", "con", "NUL.txt", "COM1", "LPT9"])
def test_windows_safe_component_suffixes_a_reserved_device_name(name):
    out = windows_safe_component(name)
    assert out != name
    assert out.rstrip("_").upper().split(".")[0] in {
        "CON", "NUL", "COM1", "LPT9"} or out.startswith(name.split(".")[0])


def test_windows_safe_component_folds_the_fullwidth_solidus_via_nfkc():
    # U+FF0F FULLWIDTH SOLIDUS has an NFKC compatibility decomposition to
    # ordinary '/' -- left alone it would silently create an extra folder
    # level the user never typed.
    assert windows_safe_component("a／b") == "a_b"


def test_windows_safe_component_folds_the_division_slash_explicitly():
    # U+2215 DIVISION SLASH has NO NFKC decomposition to '/' -- it must be
    # caught by name, not by normalisation, or it passes through untouched.
    assert windows_safe_component("a∕b") == "a_b"


def test_windows_safe_component_is_never_empty():
    assert windows_safe_component(None) == "_"
    assert windows_safe_component("   ") == "_"


def test_windows_safe_component_caps_a_very_long_name():
    # MAX_PATH budget: batch/folders/stem + " (NN)" + "_ballooned" + ext all
    # share one 260-char path, so a single component must not be able to
    # consume the whole thing on its own.
    out = windows_safe_component("x" * 300)
    assert len(out) <= 100
    assert out == "x" * len(out)


def test_windows_safe_component_cap_does_not_leave_a_trailing_dot_or_space():
    # Built so the cut at 100 characters lands exactly on a space: 99 "x"s
    # then a space, then more "x"s -- truncating naively would leave
    # "xxx...x " and Windows would silently strip it, merging it with a
    # sibling that has no trailing space.
    name = ("x" * 99) + " " + ("y" * 50)
    out = windows_safe_component(name)
    assert out[-1] not in (" ", ".")
    assert len(out) <= 101  # the mark appended after stripping may add one


def test_windows_safe_component_cap_does_not_create_a_reserved_name():
    # A name that is mostly dots after a reserved-looking prefix must not
    # come out as a bare device name once the cap strips the dots away --
    # the reserved check runs on the FINAL (capped, stripped) string.
    name = "NUL" + ("." * 300)
    out = windows_safe_component(name)
    assert out.upper() not in {"NUL", "NUL."}


def test_unique_export_path_joins_folder_and_suffixes_on_collision():
    used = set()
    first = unique_export_path(used, ["Batch 1"], "w1", ".xlsx")
    second = unique_export_path(used, ["Batch 1"], "w1", ".xlsx")
    assert first == "Batch 1/w1.xlsx"
    assert second == "Batch 1/w1 (2).xlsx"
    # `used` holds casefolded keys (Windows file systems are case-insensitive --
    # see the dedupe tests below), not the literal returned candidates.
    assert used == {first.casefold(), second.casefold()}


def test_unique_export_path_with_no_folder():
    used = set()
    assert unique_export_path(used, [], "w1", ".pdf") == "w1.pdf"


def test_unique_export_path_dedupes_case_insensitively_on_the_stem():
    # Windows file systems are case-insensitive: "Part.xlsx" and "part.xlsx"
    # are the SAME path on disk, so the second one written would silently
    # overwrite the first inside the extracted ZIP.
    used = set()
    first = unique_export_path(used, ["Batch 1"], "Part", ".xlsx")
    second = unique_export_path(used, ["Batch 1"], "part", ".xlsx")
    assert first == "Batch 1/Part.xlsx"
    # the SUFFIXED candidate keeps the case it was asked for, "part", not
    # the first writer's "Part" -- only the collision check is casefolded.
    assert second == "Batch 1/part (2).xlsx"


def test_unique_export_path_dedupes_case_insensitively_on_the_folder():
    # A folder differing only in case from an earlier one is the same
    # Windows directory too.
    used = set()
    first = unique_export_path(used, ["A"], "x", ".xlsx")
    second = unique_export_path(used, ["a"], "X", ".xlsx")
    assert first == "A/x.xlsx"
    assert second == "a/X (2).xlsx"


def test_unique_export_path_returned_name_keeps_its_original_case():
    used = set()
    out = unique_export_path(used, ["Batch 1"], "WeldSeam", ".xlsx")
    assert out == "Batch 1/WeldSeam.xlsx"
