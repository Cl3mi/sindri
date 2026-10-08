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


def test_unique_export_path_joins_folder_and_suffixes_on_collision():
    used = set()
    first = unique_export_path(used, ["Batch 1"], "w1", ".xlsx")
    second = unique_export_path(used, ["Batch 1"], "w1", ".xlsx")
    assert first == "Batch 1/w1.xlsx"
    assert second == "Batch 1/w1 (2).xlsx"
    assert used == {first, second}


def test_unique_export_path_with_no_folder():
    used = set()
    assert unique_export_path(used, [], "w1", ".pdf") == "w1.pdf"
