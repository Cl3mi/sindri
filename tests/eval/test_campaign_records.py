import json

import pytest

from app.campaign import load, pull
from app.eval.campaign_records import read_records
from tests.campaign.test_load_pull import HA, HB, HC, bundle, drain, env  # noqa: F401
from tests.eval.campaign_fixtures import (drawing, event, proposal_row,
                                          rewrite_tar, write_tar)


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


def test_a_non_string_doc_hash_is_keyed_by_dir(tmp_path):
    """A doc_hash that is not a string would mix key types (sorted() then
    raises in the grader) or be unhashable outright; the dir is always a
    string, so the record falls back to it."""
    p = [proposal_row("a")]
    path = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, []),
                                          drawing("bbbbbbbb", p, p, []),
                                          drawing("cccccccc", p, p, [])])

    def edit(n, b):
        if not n.endswith("/index.json"):
            return b
        idx = json.loads(b)
        idx["documents"][1]["doc_hash"] = 5
        idx["documents"][2]["doc_hash"] = ["x"]
        return json.dumps(idx).encode()
    rec = read_records(rewrite_tar(path, tmp_path / "k.tar", edit))
    assert set(rec.drawings) == {"aaaaaaaa", "bbbbbbbb", "cccccccc"}
    assert all(isinstance(k, str) for k in rec.drawings)


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
    # a marker that must not leak, in each of the three JSON files
    for member, word in [("proposal.json", "proposal_unreadable"),
                         ("header.json", "header_unreadable"),
                         ("sealed/r1.json", "sealed_unreadable")]:
        bad = rewrite_tar(path, tmp_path / "bad.tar", lambda n, b, m=member:
                          b"{CLIENT-SECRET" if n.endswith("/" + m) else b)
        err = read_records(bad).drawings["aaaaaaaa"].error
        assert err == word and "SECRET" not in err


def test_final_revision_without_a_sealed_file_is_sealed_missing(tmp_path):
    p = [proposal_row("a")]
    path = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, [])])
    cut = rewrite_tar(path, tmp_path / "cut.tar",
                      lambda n, b: None if "/sealed/" in n else b)
    d = read_records(cut).drawings["aaaaaaaa"]
    assert d.sealed is None and d.error == "sealed_missing"


def test_a_tail_torn_inside_a_multibyte_character_is_still_harmless(tmp_path):
    """A pull mid-append can cut a UTF-8 character; strict decoding of the
    whole file used to fail the entire campaign over the torn tail."""
    p = [proposal_row("a")]
    torn = b'{"seq": 2, "note": "' + "\u00fc".encode("utf-8")[:1]   # lone lead byte
    d = drawing("aaaaaaaa", p, p, [], events_lines=[json.dumps(event(1)), torn])
    rec = read_records(write_tar(tmp_path / "r.tar", [d]))
    assert [e["seq"] for e in rec.drawings["aaaaaaaa"].events] == [1]


def test_non_object_event_lines_end_the_list(tmp_path):
    p = [proposal_row("a")]
    for junk in ("5", "[]", '"x"', "null"):
        d = drawing("aaaaaaaa", p, p, [], events_lines=[
            json.dumps(event(1)), junk, json.dumps(event(2))])
        rec = read_records(write_tar(tmp_path / "r.tar", [d]))
        assert [e["seq"] for e in rec.drawings["aaaaaaaa"].events] == [1]


def test_an_odd_index_is_refused_without_client_text(tmp_path):
    p = [proposal_row("a")]
    path = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, [])])
    for bad in (b"{CLIENT-SECRET", b'{"documents": {"a": 1}}', b'{"documents": [5]}',
                b'{"documents": [{"doc_hash": "aaaaaaaa"}]}', b"[]", b'{"skipped": []}'):
        t = rewrite_tar(path, tmp_path / "bad.tar", lambda n, b, x=bad:
                        x if n.endswith("/index.json") else b)
        with pytest.raises(ValueError) as ei:
            read_records(t)
        assert "not a campaign pull" in str(ei.value) and "SECRET" not in str(ei.value)


def test_a_compressed_archive_is_not_a_pull(tmp_path):
    """pull.py writes a plain tar. tarfile's "r" would decompress anything it
    is handed, which turns a small file into an unbounded read."""
    import gzip
    p = [proposal_row("a")]
    raw = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, [])]).read_bytes()
    gz = tmp_path / "r.tar.gz"
    gz.write_bytes(gzip.compress(raw))
    with pytest.raises(ValueError) as ei:
        read_records(gz)
    assert "not a campaign pull" in str(ei.value)


def test_a_revision_mismatch_is_flagged(tmp_path):
    """pull.py's crash window: the sealed file was written but the index still
    names the previous revision (or the reverse)."""
    p = [proposal_row("a")]
    path = write_tar(tmp_path / "r.tar", [drawing("aaaaaaaa", p, p, []),
                                          drawing("bbbbbbbb", p, p, [])])

    def edit(n, b):
        if not n.endswith("/index.json"):
            return b
        idx = json.loads(b)
        idx["documents"][0]["sealed_revision"] = 2
        return json.dumps(idx).encode()
    rec = read_records(rewrite_tar(path, tmp_path / "m.tar", edit))
    assert rec.drawings["aaaaaaaa"].revision_mismatch is True
    assert rec.drawings["bbbbbbbb"].revision_mismatch is False


def test_the_real_pull_layout_reads_back(tmp_path, sample_pdf, env):
    """The synthetic fixture only guesses pull.py's layout; this runs the REAL
    writer (ReviewStore + pull_campaign) with a sealed drawing, a hashless one,
    a duplicate hash and a skipped one, so layout drift on either side fails."""
    data, repo, store, worker = env
    load.load_bundle(bundle(tmp_path, sample_pdf, hashes=(HA, HB, HC)), data, repo)
    load.load_bundle(bundle(tmp_path, sample_pdf, hashes=(HA,), root_name="b2",
                            salt=b"v2"), data, repo)
    drain(worker)
    docs = repo.list_documents()
    by = lambda h: [d["id"] for d in docs if d["file_name"] == f"{h}.pdf"]
    first_a, dup_a = by(HA)
    [bb], [cc] = by(HB), by(HC)
    o = store.open(first_a)
    rid = o["proposal"][0]["id"]
    store.append(first_a, o["writer"], [event(1, "accept", ids=[rid])])
    store.seal(first_a, o["writer"], 1, o["proposal"], [rid])
    repo.mark_finished(first_a, 1, 1)
    hp = store.root / bb / "header.json"
    header = json.loads(hp.read_text())
    del header["campaign"]["doc_hash"]
    hp.write_text(json.dumps(header))
    (store.root / cc / "proposal.json").unlink()
    assert pull.pull_campaign("pilot-1", repo, store, data, tmp_path / "out.tar") == 3

    rec = read_records(tmp_path / "out.tar")
    assert rec.campaign == "pilot-1"
    assert f"nohash-{bb[:8]}" in rec.drawings
    assert HA in rec.drawings
    assert any(k.startswith(f"{HA}-") for k in rec.drawings)
    assert [s["doc_hash"] for s in rec.skipped] == [HC]
    sealed_rec = next(r for r in rec.drawings.values()
                      if r.sealed and r.sealed["revision"] == 1)
    assert sealed_rec.error is None and sealed_rec.proposal_rows
    assert sealed_rec.events and "t_ms" in sealed_rec.events[0]
    assert sealed_rec.header["campaign"]["render_scale"] == 300 / 72.0
    assert not any(r.revision_mismatch for r in rec.drawings.values())
