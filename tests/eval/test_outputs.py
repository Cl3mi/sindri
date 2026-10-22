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
    """The `.partial` name is predictable; a link planted there is neither
    written through nor removed -- we did not make it, so it is refused
    (security review finding 8) and left for a human to look at."""
    gold = tmp_path / "gold.json"
    gold.write_text("GOLD")
    t = tmp_path / "t.json"
    (tmp_path / "t.json.partial").symlink_to(gold)
    with pytest.raises(OutputRefused):
        write_atomic(t, "new")
    assert gold.read_text() == "GOLD" and not t.exists()
    assert (tmp_path / "t.json.partial").is_symlink()


def test_write_atomic_refuses_a_partial_that_is_a_directory(tmp_path):
    (tmp_path / "t.json.partial").mkdir()
    with pytest.raises(OutputRefused):
        write_atomic(tmp_path / "t.json", "new")
    assert (tmp_path / "t.json.partial").is_dir()


def test_write_atomic_removes_a_stale_regular_partial(tmp_path):
    """A crash between create and rename leaves a regular `.partial` of our
    own; the next write must not be blocked by it."""
    (tmp_path / "t.json.partial").write_text("half")
    write_atomic(tmp_path / "t.json", "new")
    assert (tmp_path / "t.json").read_text() == "new"
    assert not (tmp_path / "t.json.partial").exists()


def test_write_atomic_mode(tmp_path):
    """Client-value files (class B/C) are written 0600, digests 0644."""
    from app.eval.outputs import CLIENT_FILE_MODE, DIGEST_FILE_MODE
    assert (CLIENT_FILE_MODE, DIGEST_FILE_MODE) == (0o600, 0o644)
    write_atomic(tmp_path / "c.json", "x", mode=CLIENT_FILE_MODE)
    assert (tmp_path / "c.json").stat().st_mode & 0o777 == 0o600
    write_atomic(tmp_path / "d.json", "x")
    assert (tmp_path / "d.json").stat().st_mode & 0o077 in (0o044, 0o040, 0o004, 0)


def _swap_for_link(real_dir, link_to):
    """Replace a checked directory by a link -- what a racing writer would
    do between the check and the write."""
    real_dir.rename(real_dir.with_name(real_dir.name + "-moved"))
    real_dir.symlink_to(link_to)


def test_write_atomic_refuses_a_parent_swapped_before_the_write(roots, tmp_path):
    """Security review finding 2: the checker resolved the parent; if it is
    a link by the time we write, the bytes would land wherever it points."""
    out = tmp_path / "scratch"
    out.mkdir()
    target = check_digest_out(out / "d.json", KIND)
    _swap_for_link(out, roots / "gold")
    with pytest.raises(OutputRefused):
        write_atomic(target, "x", expect_parent=target.parent)
    assert list((roots / "gold").iterdir()) == []


def test_write_atomic_refuses_a_parent_swapped_during_the_write(
        roots, tmp_path, monkeypatch):
    """The second look is immediately before the rename, which is the step
    that makes the file visible under its name."""
    out = tmp_path / "scratch"
    out.mkdir()
    target = check_digest_out(out / "d.json", KIND)
    real_fsync = os.fsync
    swapped = []

    def fsync_then_swap(fd):
        real_fsync(fd)
        if not swapped:
            swapped.append(True)
            _swap_for_link(out, roots / "gold")
    monkeypatch.setattr(os, "fsync", fsync_then_swap)
    with pytest.raises(OutputRefused):
        write_atomic(target, "x", expect_parent=target.parent)
    assert swapped and list((roots / "gold").iterdir()) == []


def test_write_json_digest_checks_its_parent_too(roots, tmp_path):
    from app.eval.outputs import write_json_digest
    out = tmp_path / "scratch"
    out.mkdir()
    target = check_digest_out(out / "d.json", KIND)
    _swap_for_link(out, roots / "gold")
    with pytest.raises(OutputRefused):
        write_json_digest(target, KIND, {"n": 1})
    assert list((roots / "gold").iterdir()) == []


def test_write_json_digest_checks_the_nearest_existing_ancestor(roots, tmp_path):
    """The digest's own folder may not exist yet (it is created on write);
    a swap one level higher must still be caught before mkdir."""
    from app.eval.outputs import write_json_digest
    out = tmp_path / "scratch"
    out.mkdir()
    target = check_digest_out(out / "new" / "deeper" / "d.json", KIND)
    _swap_for_link(out, roots / "gold")
    with pytest.raises(OutputRefused):
        write_json_digest(target, KIND, {"n": 1})
    assert list((roots / "gold").iterdir()) == []


def test_claim_dir_checks_its_parent(roots):
    from app.eval.outputs import check_client_dir_out as dir_out, claim_dir as claim
    target = dir_out(roots / "runs" / "r9", subdir="runs", legacy=("*.pred.json",))
    _swap_for_link(roots / "runs", roots / "gold")
    with pytest.raises(OutputRefused):
        claim(target)
    assert list((roots / "gold").iterdir()) == []


# ---- security review findings 4, 6, 7 ---------------------------------------

@pytest.mark.parametrize("call", [
    lambda p: check_digest_out(p / "a\x00b.json", KIND),
    lambda p: check_digest_out(p / "d.json", KIND, avoid=[str(p / "x\x00")]),
    lambda p: write_atomic(p / "a\x00b.json", "x"),
])
def test_nul_byte_is_a_fixed_refusal(roots, tmp_path, call):
    """A NUL byte makes the OS calls raise ValueError, which would escape as
    a traceback carrying the path."""
    with pytest.raises(OutputRefused) as e:
        call(tmp_path)
    assert str(tmp_path) not in str(e.value)


def test_nul_byte_in_client_checks_is_a_fixed_refusal(roots):
    from app.eval.outputs import check_client_dir_out as dir_out
    with pytest.raises(OutputRefused):
        check_client_file_out(roots / "reports" / "a\x00.report.json",
                              subdir="reports", suffix=".report.json")
    with pytest.raises(OutputRefused):
        dir_out(roots / "runs" / "r\x00", subdir="runs", legacy=("*.pred.json",))


def test_target_inside_an_input_directory_refused(roots, tmp_path):
    """`--run <dir>` is an input; a digest written INTO it is a new file in
    a directory the run is reading (and may glob)."""
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(OutputRefused):
        check_digest_out(run / "d.json", KIND, avoid=[run])
    with pytest.raises(OutputRefused):
        check_digest_out(run / "sub" / "d.json", KIND, avoid=[run])
    assert check_digest_out(tmp_path / "run-sibling.json", KIND, avoid=[run])


@pytest.mark.parametrize("parts", [(".git", "hooks", "x.json"),
                                   (".claude", "settings.json"),
                                   ("repo", ".git", "config.json")])
def test_digest_into_git_or_claude_config_refused(roots, tmp_path, parts):
    """A digest there is code or configuration an agent's tools then run
    (hooks, settings), not a measurement."""
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path.joinpath(*parts), KIND)


def test_digest_through_a_link_into_dot_git_refused(roots, tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "innocent").symlink_to(tmp_path / "repo" / ".git")
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "innocent" / "x.json", KIND)


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


# ---- class B-file: one client-value (or frozen) file ---------------------

from app.eval.outputs import (  # noqa: E402
    DIR_KIND, DIR_MARKER, check_client_dir_out, check_client_file_out,
    claim_dir)


def test_file_out_must_be_direct_child_of_subdir(roots):
    """Operator decision 2: client values land in the root's fixed
    subfolders, not anywhere inside it -- `gold/` and `corpus/` are inputs."""
    ok = roots / "reports" / "a.report.json"
    assert check_client_file_out(ok, subdir="reports", suffix=".report.json")
    for bad in (roots / "gold" / "a.report.json",                 # wrong subfolder
                roots / "reports" / "sub" / "a.report.json",      # grandchild
                roots / "a.report.json"):                         # root itself
        with pytest.raises(OutputRefused):
            check_client_file_out(bad, subdir="reports", suffix=".report.json")


def test_file_out_dotdot_out_of_subdir_refused(roots):
    with pytest.raises(OutputRefused):
        check_client_file_out(roots / "reports" / ".." / "gold" / "a.report.json",
                              subdir="reports", suffix=".report.json")


def test_file_out_parent_symlink_resolves_before_the_subdir_rule(roots, tmp_path):
    """A link outside the root that points INTO reports/ is judged by where
    it lands; one that points into gold/ is refused."""
    (tmp_path / "to_reports").symlink_to(roots / "reports")
    (tmp_path / "to_gold").symlink_to(roots / "gold")
    assert check_client_file_out(tmp_path / "to_reports" / "a.report.json",
                                 subdir="reports", suffix=".report.json") \
        == roots / "reports" / "a.report.json"
    with pytest.raises(OutputRefused):
        check_client_file_out(tmp_path / "to_gold" / "a.report.json",
                              subdir="reports", suffix=".report.json")


def test_file_out_outside_root_refused_when_roots_configured(roots, tmp_path):
    with pytest.raises(OutputRefused):
        check_client_file_out(tmp_path / "a.report.json", subdir="reports",
                              suffix=".report.json")


def test_file_out_no_roots_configured_skips_only_the_root_rule(tmp_path, monkeypatch):
    """Operator decision 3: the GPU container and CI have no roots file;
    there only the location rule is skipped -- suffix, link and overwrite
    rules still hold."""
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(tmp_path / "absent.txt"))
    assert check_client_file_out(tmp_path / "a.report.json", subdir="reports",
                                 suffix=".report.json")
    with pytest.raises(OutputRefused):                              # suffix still applies
        check_client_file_out(tmp_path / "a.txt", subdir="reports",
                              suffix=".report.json")
    (tmp_path / "real.report.json").write_text("{}")
    (tmp_path / "l.report.json").symlink_to(tmp_path / "real.report.json")
    with pytest.raises(OutputRefused):                              # link still applies
        check_client_file_out(tmp_path / "l.report.json", subdir="reports",
                              suffix=".report.json")
    with pytest.raises(OutputRefused):                              # overwrite still applies
        check_client_file_out(tmp_path / "real.report.json", subdir="reports",
                              suffix=".report.json", kind="run-report")


def test_file_out_suffix_alone_is_not_a_name(roots):
    with pytest.raises(OutputRefused):
        check_client_file_out(roots / "reports" / ".report.json",
                              subdir="reports", suffix=".report.json")


def test_file_out_never_overwrites_without_kind(roots):
    """The split is frozen at 6d174d5e4f1b9228 and the variants list feeds
    it: with kind=None no existing file is ever replaced, marked or not."""
    t = roots / "meta" / "splits.json"
    t.write_text("{}")
    with pytest.raises(OutputRefused):
        check_client_file_out(t, subdir="meta", suffix=".json")
    t.write_text(json.dumps({"kind": "run-report"}))
    with pytest.raises(OutputRefused):
        check_client_file_out(t, subdir="meta", suffix=".json")


def test_report_replaced_only_when_marked(roots):
    t = roots / "reports" / "r.report.json"
    t.write_text('{"schema_version": 1}')
    with pytest.raises(OutputRefused):
        check_client_file_out(t, subdir="reports", suffix=".report.json",
                              kind="run-report")
    t.write_text('{"kind": "run-report", "schema_version": 1}')
    assert check_client_file_out(t, subdir="reports", suffix=".report.json",
                                 kind="run-report")


def test_report_hardlinked_to_gold_refused(roots):
    """A second name for a gold file carries gold's bytes, so no marker."""
    gold = roots / "gold" / "x.gold.json"
    gold.write_text("GOLD")
    t = roots / "reports" / "r.report.json"
    os.link(gold, t)
    with pytest.raises(OutputRefused):
        check_client_file_out(t, subdir="reports", suffix=".report.json",
                              kind="run-report")


def test_file_out_symlink_to_gold_refused(roots):
    gold = roots / "gold" / "x.gold.json"
    gold.write_text("GOLD")
    link = roots / "reports" / "r.report.json"
    link.symlink_to(gold)
    with pytest.raises(OutputRefused):
        check_client_file_out(link, subdir="reports", suffix=".report.json",
                              kind="run-report")


def test_file_out_equal_to_an_input_refused(roots):
    t = roots / "reports" / "r.report.json"
    t.write_text('{"kind": "run-report"}')
    with pytest.raises(OutputRefused):
        check_client_file_out(t, subdir="reports", suffix=".report.json",
                              kind="run-report", avoid=[t])


# ---- class C: a directory of client-value files --------------------------

LEG = ("*.pred.json", ".complete")


def test_dir_out_new_empty_marked_legacy_ok(roots):
    new = roots / "runs" / "r6"
    assert check_client_dir_out(new, subdir="runs", legacy=LEG)
    new.mkdir()
    assert check_client_dir_out(new, subdir="runs", legacy=LEG)       # empty
    (new / "a.pred.json").write_text("{}")
    (new / ".complete").write_text("")
    # A run written before this check carries no marker; predict resume on
    # the GPU host is load bearing, so a dir of only its own file shapes
    # still counts as ours.
    assert check_client_dir_out(new, subdir="runs", legacy=LEG)
    claim_dir(new)
    (new / "other.txt").write_text("x")
    assert check_client_dir_out(new, subdir="runs", legacy=LEG)       # marker => ours


def test_claim_dir_creates_marks_and_is_idempotent(roots):
    from app.eval.review import _carries_marker
    d = roots / "runs" / "fresh"
    claim_dir(d)
    claim_dir(d)
    assert _carries_marker(d / DIR_MARKER, DIR_KIND)
    assert sorted(p.name for p in d.iterdir()) == [DIR_MARKER]


def test_dir_out_refuses_foreign_dir_gold_dir_and_wrong_place(roots):
    gold = roots / "gold" / "g"
    gold.mkdir()
    (gold / "a.gold.json").write_text("GOLD")
    with pytest.raises(OutputRefused):
        check_client_dir_out(gold, subdir="runs", legacy=LEG)          # wrong subfolder
    r = roots / "runs" / "r"
    r.mkdir()
    (r / "note.txt").write_text("x")
    with pytest.raises(OutputRefused):
        check_client_dir_out(r, subdir="runs", legacy=LEG)             # foreign file, no marker
    with pytest.raises(OutputRefused):
        check_client_dir_out(roots / "runs" / "a" / "b", subdir="runs",
                             legacy=LEG)                               # grandchild
    with pytest.raises(OutputRefused):
        check_client_dir_out(roots / "runs", subdir="runs", legacy=LEG)  # the subfolder itself


def test_dir_out_legacy_shapes_are_per_tool(roots):
    """An ingest dir (gold files) is not a predict dir: aiming predict --out
    at gold must not pass as 'legacy' just because the dir is all JSON."""
    d = roots / "runs" / "looks-like-gold"
    d.mkdir()
    (d / "a.gold.json").write_text("GOLD")
    (d / "doc_id_map.json").write_text("{}")
    with pytest.raises(OutputRefused):
        check_client_dir_out(d, subdir="runs", legacy=LEG)
    g = roots / "gold" / "ingest1"
    g.mkdir()
    (g / "a.gold.json").write_text("{}")
    (g / "doc_id_map.json").write_text("{}")
    assert check_client_dir_out(g, subdir="gold",
                                legacy=("*.gold.json", "doc_id_map.json"))


def test_dir_marker_must_carry_its_kind(roots):
    """The marker is what lets a dir hold ANY file; an empty or foreign file
    that merely has the marker's name must not unlock that."""
    d = roots / "runs" / "spoof"
    d.mkdir()
    (d / DIR_MARKER).write_text("")
    (d / "note.txt").write_text("x")
    with pytest.raises(OutputRefused):
        check_client_dir_out(d, subdir="runs", legacy=LEG)
    (d / DIR_MARKER).write_text(json.dumps({"kind": "something-else"}))
    with pytest.raises(OutputRefused):
        check_client_dir_out(d, subdir="runs", legacy=LEG)


def test_dir_out_refuses_symlink_entry_subdir_and_symlink_dir(roots, tmp_path):
    gold = roots / "gold" / "x.gold.json"
    gold.write_text("GOLD")
    r = roots / "runs" / "r"
    r.mkdir()
    (r / "a.pred.json").symlink_to(gold)
    with pytest.raises(OutputRefused):
        check_client_dir_out(r, subdir="runs", legacy=LEG)
    claim_dir(r)                       # even a marked dir: a link is never ours
    with pytest.raises(OutputRefused):
        check_client_dir_out(r, subdir="runs", legacy=LEG)
    r2 = roots / "runs" / "r2"
    r2.mkdir()
    (r2 / "sub").mkdir()
    with pytest.raises(OutputRefused):
        check_client_dir_out(r2, subdir="runs", legacy=LEG)
    link = roots / "runs" / "r3"
    link.symlink_to(roots / "gold")
    with pytest.raises(OutputRefused):
        check_client_dir_out(link, subdir="runs", legacy=LEG)


def test_dir_out_existing_file_at_target_refused(roots):
    f = roots / "runs" / "r"
    f.write_text("x")
    with pytest.raises(OutputRefused):
        check_client_dir_out(f, subdir="runs", legacy=LEG)


def test_dir_out_equal_to_an_input_refused(roots):
    """`verify --run X --out X` would rewrite the dumps it is reading."""
    r = roots / "runs" / "r"
    r.mkdir()
    (r / "a.pred.json").write_text("{}")
    with pytest.raises(OutputRefused):
        check_client_dir_out(r, subdir="runs", legacy=LEG, avoid=[r])


def test_dir_out_no_roots_configured_keeps_the_ours_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(tmp_path / "absent.txt"))
    assert check_client_dir_out(tmp_path / "out", subdir="runs", legacy=LEG)
    d = tmp_path / "foreign"
    d.mkdir()
    (d / "x.txt").write_text("x")
    with pytest.raises(OutputRefused):
        check_client_dir_out(d, subdir="runs", legacy=LEG)


def test_dir_out_unreadable_dir_is_a_fixed_refusal(roots):
    """An OSError from listing must become a fixed sentence, not a
    traceback carrying the path."""
    d = roots / "runs" / "locked"
    d.mkdir()
    (d / "a.pred.json").write_text("{}")
    d.chmod(0)
    try:
        if os.access(d, os.R_OK):
            pytest.skip("running as a user that ignores permissions")
        with pytest.raises(OutputRefused) as e:
            check_client_dir_out(d, subdir="runs", legacy=LEG)
        assert "locked" not in str(e.value)
    finally:
        d.chmod(0o755)


# ---- the RunReport marker ------------------------------------------------

def _report():
    from app.eval.models import (MatchParams, ReviewCostWeights, RunConfig,
                                 RunReport)
    return RunReport(run_name="stub", config=RunConfig(model_id="stub", dpi=300),
                     weights=ReviewCostWeights(), match_params=MatchParams())


def test_run_report_dumps_kind_as_its_first_key():
    """`score --out` may replace a report only if it carries the marker as
    its FIRST key -- the marker peek reads the opening bytes and never
    parses a (large) report. `schema_version` lives on the base class and
    would otherwise dump first."""
    text = _report().model_dump_json(indent=1)
    assert text.startswith('{\n "kind": "run-report"')
    assert list(_report().model_dump())[0] == "kind"


def test_run_report_without_kind_still_loads():
    """Every report written before the marker lacks it; they must stay
    readable (summary, compare), and SCHEMA_VERSION stays 1."""
    from app.eval.models import SCHEMA_VERSION, RunReport
    payload = json.loads(_report().model_dump_json())
    del payload["kind"]
    old = RunReport.model_validate_json(json.dumps(payload))
    assert old.kind == "run-report"
    assert old.schema_version == SCHEMA_VERSION == 1


def test_run_report_of_another_kind_is_rejected_loudly():
    """A file labelled as something else (a grade report, a digest) must not
    be misread as a run report."""
    from app.eval.models import RunReport
    payload = json.loads(_report().model_dump_json())
    payload["kind"] = "campaign-grade-report"
    with pytest.raises(ValueError):
        RunReport.model_validate(payload)


def test_run_report_roundtrips():
    from app.eval.models import RunReport
    r = _report()
    assert RunReport.model_validate_json(r.model_dump_json()) == r


def test_written_report_carries_the_marker_and_is_replaceable(roots):
    from app.eval.review import _carries_marker
    t = roots / "reports" / "r.report.json"
    write_atomic(t, _report().model_dump_json(indent=1))
    assert _carries_marker(t, "run-report")
    assert check_client_file_out(t, subdir="reports", suffix=".report.json",
                                 kind="run-report") == t
