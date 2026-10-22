"""Where an agent-chosen runner output may write (H1, design 2026-10-23).

The Bash guard allowlists runner subcommands by their SHAPE, so every `--out`
style flag is a write path an agent picks. These tests are the exploits an
agent could try -- a link to gold, a parent link into the root, `..`
traversal, a hardlink, a file of another kind -- each against a temporary
protected root, never the real one."""
import json
import os

import pytest

from app.eval.outputs import OutputRefused, check_digest_out, write_atomic

KIND = "run-summary-digest"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A synthetic protected root laid out like the client corpus, listed in
    a roots file that SINDRI_PROTECTED_PATHS points at -- so no test can
    consult (or depend on) the machine's real roots file."""
    root = tmp_path / "client"
    for sub in ("reports", "meta", "runs", "gold"):
        (root / sub).mkdir(parents=True)
    roots_file = tmp_path / "roots.txt"
    roots_file.write_text(str(root) + "\n")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(roots_file))
    return root


# ---- class A: counts-only digests --------------------------------------

def test_digest_inside_protected_root_refused(roots):
    """A digest is the agent's view; inside the root it could never be read,
    and an agent-picked path there could replace client data."""
    with pytest.raises(OutputRefused):
        check_digest_out(roots / "reports" / "x.json", KIND)


def test_digest_outside_root_new_file_ok(roots, tmp_path):
    target = tmp_path / "scratch" / "d.json"
    assert check_digest_out(target, KIND) == target.resolve()


def test_digest_symlink_refused_even_to_a_safe_target(roots, tmp_path):
    """Checked BEFORE resolve(): resolving would follow the link, and the
    next link could point at gold. A link is never a legitimate output."""
    real = tmp_path / "real.json"
    real.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(OutputRefused):
        check_digest_out(link, KIND)


def test_digest_dangling_symlink_refused(roots, tmp_path):
    """A dangling link reports exists() False, so a check that only looked
    at existing files would let the write create the link's target -- which
    could be anywhere, the root included."""
    link = tmp_path / "dangling.json"
    link.symlink_to(roots / "reports" / "new.json")
    with pytest.raises(OutputRefused):
        check_digest_out(link, KIND)


def test_digest_parent_symlink_into_root_refused(roots, tmp_path):
    (tmp_path / "hop").symlink_to(roots / "reports")
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "hop" / "d.json", KIND)


def test_digest_dangling_parent_symlink_into_root_refused(roots, tmp_path):
    """The parent link points at a directory that does not exist YET; the
    writer's mkdir would create it inside the root."""
    (tmp_path / "hop").symlink_to(roots / "reports" / "later")
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "hop" / "d.json", KIND)


def test_digest_dotdot_into_root_refused(roots, tmp_path):
    (tmp_path / "a").mkdir()
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "a" / ".." / "client" / "reports" / "d.json",
                         KIND)


def test_digest_dotdot_through_missing_dir_into_root_refused(roots, tmp_path):
    """`..` after a component that does not exist must still collapse, or a
    lexical check would see a path outside the root."""
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "nope" / ".." / "client" / "meta" / "d.json",
                         KIND)


def test_digest_wrong_suffix_refused(roots, tmp_path):
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "d.txt", KIND)
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / ".json", KIND)


def test_existing_foreign_file_refused_marked_file_replaced(roots, tmp_path):
    """Operator decision 1: a digest may replace only its own previous
    output -- the same KIND, as the first key."""
    t = tmp_path / "d.json"
    t.write_text('{"a": 1}')
    with pytest.raises(OutputRefused):
        check_digest_out(t, KIND)
    t.write_text(json.dumps({"kind": KIND, "n": 1}))
    assert check_digest_out(t, KIND)
    t.write_text(json.dumps({"kind": "other-kind"}))
    with pytest.raises(OutputRefused):
        check_digest_out(t, KIND)


def test_marker_not_first_key_does_not_count(roots, tmp_path):
    """Only the opening bytes are peeked: a `kind` buried later in a file
    (say a value quoted inside a report) must not make it replaceable."""
    t = tmp_path / "d.json"
    t.write_text(json.dumps({"a": 1, "kind": KIND}))
    with pytest.raises(OutputRefused):
        check_digest_out(t, KIND)


def test_marker_prefix_of_another_kind_does_not_count(roots, tmp_path):
    t = tmp_path / "d.json"
    t.write_text(json.dumps({"kind": KIND + "-x"}))
    with pytest.raises(OutputRefused):
        check_digest_out(t, KIND)


def test_existing_directory_at_target_refused(roots, tmp_path):
    (tmp_path / "d.json").mkdir()
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "d.json", KIND)


def test_digest_equal_to_an_input_refused_even_when_marked(roots, tmp_path):
    """`summary --out <its own input report>` would destroy what it reads."""
    t = tmp_path / "d.json"
    t.write_text(json.dumps({"kind": KIND}))
    with pytest.raises(OutputRefused):
        check_digest_out(t, KIND, avoid=[t])


def test_input_named_by_another_spelling_refused(roots, tmp_path):
    t = tmp_path / "d.json"
    t.write_text(json.dumps({"kind": KIND}))
    (tmp_path / "sub").mkdir()
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "sub" / ".." / "d.json", KIND, avoid=[str(t)])


def test_input_aliased_by_hardlink_refused(roots, tmp_path):
    a = tmp_path / "in.json"
    a.write_text(json.dumps({"kind": KIND}))
    b = tmp_path / "out.json"
    os.link(a, b)
    with pytest.raises(OutputRefused):
        check_digest_out(b, KIND, avoid=[a])


def test_empty_avoid_entries_are_ignored(roots, tmp_path):
    """Optional inputs arrive as None or ""; they name nothing."""
    assert check_digest_out(tmp_path / "d.json", KIND, avoid=[None, ""])


def test_refusals_never_echo_the_path(roots, tmp_path):
    """Messages print where an agent reads, and a path can be client text
    (a part number in a file name)."""
    secret = tmp_path / "PART-4711"
    secret.mkdir()
    t = secret / "d.json"
    t.write_text("{}")
    with pytest.raises(OutputRefused) as e:
        check_digest_out(t, KIND)
    assert "PART-4711" not in str(e.value)


# ---- the atomic writer ---------------------------------------------------

def test_write_atomic_replaces_a_hardlink_instead_of_writing_through(tmp_path):
    gold = tmp_path / "gold.json"
    gold.write_text("GOLD")
    t = tmp_path / "t.json"
    os.link(gold, t)
    write_atomic(t, "new")
    assert gold.read_text() == "GOLD" and t.read_text() == "new"


def test_write_atomic_replaces_a_symlink_not_its_target(tmp_path):
    gold = tmp_path / "gold.json"
    gold.write_text("GOLD")
    t = tmp_path / "t.json"
    t.symlink_to(gold)
    write_atomic(t, "new")
    assert gold.read_text() == "GOLD" and not t.is_symlink()


def test_write_atomic_does_not_follow_a_planted_partial_link(tmp_path):
    """The `.partial` name is predictable; a link planted there must be
    removed, not opened and written through."""
    gold = tmp_path / "gold.json"
    gold.write_text("GOLD")
    t = tmp_path / "t.json"
    (tmp_path / "t.json.partial").symlink_to(gold)
    write_atomic(t, "new")
    assert gold.read_text() == "GOLD" and t.read_text() == "new"
    assert not (tmp_path / "t.json.partial").exists()


def test_write_atomic_accepts_bytes(tmp_path):
    t = tmp_path / "t.bin"
    write_atomic(t, b"\x00\x01")
    assert t.read_bytes() == b"\x00\x01"


def test_write_atomic_leaves_no_partial_and_message_has_no_path(tmp_path):
    t = tmp_path / "missing_dir" / "t.json"
    with pytest.raises(OutputRefused) as e:
        write_atomic(t, "x")          # parent absent: caller must create it
    assert str(tmp_path) not in str(e.value)
    assert not (tmp_path / "missing_dir").exists()


def test_write_atomic_onto_a_directory_refused_and_cleans_up(tmp_path):
    (tmp_path / "t.json").mkdir()
    with pytest.raises(OutputRefused):
        write_atomic(tmp_path / "t.json", "x")
    assert not (tmp_path / "t.json.partial").exists()


def test_write_json_digest_puts_kind_first(roots, tmp_path):
    """`kind` must be the FIRST key: that is all the marker peek reads, so a
    digest written any other way could never be replaced by its own re-run."""
    from app.eval.outputs import write_json_digest
    from app.eval.review import _carries_marker
    target = check_digest_out(tmp_path / "out" / "d.json", KIND)
    # A digest dict that already carries a `kind` (a field it reports, or a
    # copy of an input's marker) must not displace the writer's own: the
    # kind argument wins, or the next run could not replace this file.
    text = write_json_digest(target, KIND, {"n": 3, "kind": "spoof"})
    assert json.loads(target.read_text()) == json.loads(text)
    assert json.loads(text) == {"kind": KIND, "n": 3}
    assert _carries_marker(target, KIND)
    assert check_digest_out(target, KIND) == target
