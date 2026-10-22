import io
import json
import tarfile

from app.eval.campaign_records import read_records
from tests.eval.campaign_fixtures import drawing, event, proposal_row, write_tar


def test_reads_index_and_each_drawing_in_place(tmp_path):
    p = [proposal_row("a")]
    tar = write_tar(tmp_path / "r.tar", [
        drawing("aaaaaaaa", p, p, []),
        drawing("bbbbbbbb", p, p, [], approved=False)])
    rec = read_records(tar)
    assert rec.campaign == "pilot-1"
    assert set(rec.drawings) == {"aaaaaaaa", "bbbbbbbb"}
    a = rec.drawings["aaaaaaaa"]
    assert a.header["campaign"]["doc_hash"] == "aaaaaaaa"
    assert a.proposal_rows[0]["id"] == "a" and a.sealed["revision"] == 1
    assert a.error is None
    assert rec.drawings["bbbbbbbb"].sealed is None          # pending
    assert rec.drawings["bbbbbbbb"].error is None           # unapproved is not damage


def test_a_torn_events_tail_is_ignored_not_fatal(tmp_path):
    p = [proposal_row("a")]
    d = drawing("aaaaaaaa", p, p, [], events_lines=[json.dumps(event(1)), '{"seq": 2, "t_m'])
    rec = read_records(write_tar(tmp_path / "r.tar", [d]))
    assert [e["seq"] for e in rec.drawings["aaaaaaaa"].events] == [1]
    assert "t_wall" in rec.drawings["aaaaaaaa"].events[0]


def test_skipped_drawings_are_carried(tmp_path):
    skip = [{"id8": "deadbeef", "state": "ready", "reason": "no_proposal",
             "doc_hash": "cccccccc", "group": "problem"}]
    rec = read_records(write_tar(tmp_path / "r.tar", [], skipped=skip))
    assert rec.skipped == skip


def test_null_hash_and_collision_dirs_are_keyed_by_dir(tmp_path):
    """pull.py packs a hashless drawing under nohash-<id8> and a second drawing
    with the same hash under <hash>-<id8>; neither may collide or be keyed as a
    hash, or one review would overwrite another in the grader."""
    p = [proposal_row("a")]
    rec = read_records(write_tar(tmp_path / "r.tar", [
        drawing("aaaaaaaa", p, p, []),
        drawing("aaaaaaaa", p, p, [], dir="aaaaaaaa-1234abcd"),
        drawing(None, p, p, [], dir="nohash-deadbeef")]))
    assert set(rec.drawings) == {"aaaaaaaa", "aaaaaaaa-1234abcd", "nohash-deadbeef"}
    assert rec.drawings["nohash-deadbeef"].index["doc_hash"] is None
    assert rec.drawings["aaaaaaaa-1234abcd"].dir == "aaaaaaaa-1234abcd"


def test_a_missing_header_marks_that_drawing_only(tmp_path):
    p = [proposal_row("a")]
    rec = read_records(write_tar(tmp_path / "r.tar", [
        drawing("aaaaaaaa", p, p, [], header=False),
        drawing("bbbbbbbb", p, p, [])]))
    assert rec.drawings["aaaaaaaa"].error == "header_missing"
    assert rec.drawings["aaaaaaaa"].header == {}
    assert rec.drawings["bbbbbbbb"].error is None


def test_unreadable_files_carry_a_type_word_never_text(tmp_path):
    p = [proposal_row("a")]
    path = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, [])])
    # rewrite with a corrupt proposal containing a marker that must not leak
    with tarfile.open(path) as t:
        items = {m.name: t.extractfile(m).read() for m in t.getmembers()}
    items["pilot-1/aaaaaaaa/proposal.json"] = b"{CLIENT-SECRET"
    bad = tmp_path / "bad.tar"
    with tarfile.open(bad, "w") as t:
        for n, b in items.items():
            i = tarfile.TarInfo(n)
            i.size = len(b)
            t.addfile(i, io.BytesIO(b))
    rec = read_records(bad)
    err = rec.drawings["aaaaaaaa"].error
    assert err == "proposal_unreadable" and "SECRET" not in err


def test_final_revision_without_a_sealed_file_is_sealed_missing(tmp_path):
    p = [proposal_row("a")]
    path = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, [])])
    with tarfile.open(path) as t:
        items = {m.name: t.extractfile(m).read() for m in t.getmembers()
                 if "/sealed/" not in m.name}
    cut = tmp_path / "cut.tar"
    with tarfile.open(cut, "w") as t:
        for n, b in items.items():
            i = tarfile.TarInfo(n)
            i.size = len(b)
            t.addfile(i, io.BytesIO(b))
    d = read_records(cut).drawings["aaaaaaaa"]
    assert d.sealed is None and d.error == "sealed_missing"
