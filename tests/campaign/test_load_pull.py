"""In-container loader and packer (campaign design §2, §4). Synthetic bundle;
the worker runs for real, so a loaded drawing ends `ready` exactly like an
extracted one."""
import json
import shutil
import tarfile

import pytest

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.worker import Worker
from app.campaign import load, pull
from app.models import Characteristic
from app.review.store import ReviewStore


@pytest.fixture
def env(tmp_path):
    data = DataDir(tmp_path / "data")
    conn = db.connect(data.db_path)
    db.migrate(conn)
    repo, store = Repo(conn), ReviewStore(tmp_path / "reviews")
    def no_gpu(*a):
        raise AssertionError("a campaign drawing must never be extracted")
    worker = Worker(repo, data, store, no_gpu, lambda pdf: {"h": 1})
    yield data, repo, store, worker
    worker.stop()
    conn.close()


def bundle(tmp_path, sample_pdf, hashes=("aa", "bb"), root_name="bundle", salt=b""):
    root = tmp_path / root_name
    for h in hashes:
        e = root / h
        e.mkdir(parents=True)
        # distinct bytes per entry, or they would be one drawing
        (e / "input.pdf").write_bytes(sample_pdf.read_bytes() + h.encode() + salt)
        (e / "page.png").write_bytes(b"png")
        rows = [Characteristic(pos=1, id=f"{h}1", nominal="1").model_dump(mode="json")]
        (e / "result.json").write_text(json.dumps(
            {"rows": rows, "notes": None, "marks": None, "title_block": []}))
        (e / "campaign.json").write_text(json.dumps(
            {"campaign": "pilot-1", "group": "great", "source_run": "r", "doc_hash": h,
             "file_name": f"{h}.pdf"}))
    return root


def drain(worker):
    while worker.run_once():
        pass


def test_load_queues_every_entry_and_the_worker_makes_them_ready(tmp_path, sample_pdf, env):
    data, repo, store, worker = env
    out = load.load_bundle(bundle(tmp_path, sample_pdf), data, repo)
    assert out == {"loaded": 2, "skipped": 0}
    drain(worker)
    docs = repo.list_documents()
    assert {d["state"] for d in docs} == {"ready"} and {d["campaign"] for d in docs} == {"pilot-1"}
    assert sorted(d["file_name"] for d in docs) == ["aa.pdf", "bb.pdf"]


def test_loading_the_same_bundle_twice_is_a_no_op(tmp_path, sample_pdf, env):
    data, repo, _, _ = env
    b = bundle(tmp_path, sample_pdf)
    load.load_bundle(b, data, repo)
    assert load.load_bundle(b, data, repo) == {"loaded": 0, "skipped": 2}


def test_a_malformed_proposal_refuses_the_whole_bundle(tmp_path, sample_pdf, env):
    data, repo, _, _ = env
    b = bundle(tmp_path, sample_pdf)
    (b / "bb" / "result.json").write_text(json.dumps({"rows": [{"pos": "x"}]}))
    with pytest.raises(load.BundleError):
        load.load_bundle(b, data, repo)
    assert repo.list_documents() == []   # validated before anything is written


def test_a_refusal_never_quotes_a_client_value(tmp_path, sample_pdf, env):
    """A pydantic message quotes the offending input; the bundle carries client
    text (CLAUDE.md §1), so a refusal names the entry and the error TYPE only."""
    data, repo, _, _ = env
    b = bundle(tmp_path, sample_pdf)
    (b / "bb" / "result.json").write_text(json.dumps({"rows": [{"pos": "SECRET-Ø42"}]}))
    with pytest.raises(load.BundleError) as e:
        load.load_bundle(b, data, repo)
    assert "SECRET" not in str(e.value) and "bb" in str(e.value)


def test_temp_dirs_of_an_interrupted_build_are_skipped(tmp_path, sample_pdf, env):
    """campaign_build.py builds into `.<hash>.tmp` and renames at the end; a
    hard kill can leave a COMPLETE temp dir. It was never published, so it is
    not an entry -- loading it would put a drawing in front of the client
    that the operator's build never finished."""
    data, repo, _, _ = env
    b = bundle(tmp_path, sample_pdf)
    shutil.copytree(b / "aa", b / ".cc.tmp")
    assert load.load_bundle(b, data, repo) == {"loaded": 2, "skipped": 0}


def test_an_entry_named_unlike_its_doc_hash_refuses_the_bundle(tmp_path, sample_pdf, env):
    """The dir name is what the operator selected by; campaign.json's doc_hash
    is what grading joins on. If they disagree one of them is wrong, and a
    drawing graded under the wrong hash poisons the result silently."""
    data, repo, _, _ = env
    b = bundle(tmp_path, sample_pdf)
    facts = json.loads((b / "bb" / "campaign.json").read_text())
    (b / "bb" / "campaign.json").write_text(json.dumps({**facts, "doc_hash": "zz"}))
    with pytest.raises(load.BundleError):
        load.load_bundle(b, data, repo)
    assert repo.list_documents() == [] and list(data.docs.iterdir()) == []


def test_an_unreadable_drawing_refuses_the_bundle_before_anything_is_written(
        tmp_path, sample_pdf, env):
    data, repo, _, _ = env
    b = bundle(tmp_path, sample_pdf)
    (b / "bb" / "input.pdf").write_bytes(b"not a drawing at all")
    with pytest.raises(load.BundleError) as e:
        load.load_bundle(b, data, repo)
    assert "bb" in str(e.value)
    assert repo.list_documents() == [] and list(data.docs.iterdir()) == []


def test_a_failed_copy_leaves_neither_a_dir_nor_a_row(tmp_path, sample_pdf, env, monkeypatch):
    """The row is what queues a drawing; created over a half-copied dir, the
    worker would fail it (or, with result.json but no input, review a drawing
    with nothing to show). So files first, row last, and a failure in between
    removes the dir."""
    data, repo, _, _ = env
    real = shutil.copyfile
    calls = []
    def flaky(src, dst, *a, **kw):
        calls.append(src)
        if len(calls) == 3:
            raise OSError("disk full at /secret/path")
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(load.shutil, "copyfile", flaky)
    with pytest.raises(load.BundleError) as e:
        load.load_bundle(bundle(tmp_path, sample_pdf), data, repo)
    assert "secret" not in str(e.value)
    assert repo.list_documents() == [] and list(data.docs.iterdir()) == []


def test_pull_packs_the_campaigns_review_records_and_an_index(tmp_path, sample_pdf, env):
    data, repo, store, worker = env
    load.load_bundle(bundle(tmp_path, sample_pdf), data, repo)
    drain(worker)
    tar_path = tmp_path / "out.tar"
    n = pull.pull_campaign("pilot-1", repo, store, tar_path)
    assert n == 2
    with tarfile.open(tar_path) as t:
        names = t.getnames()
        index = json.load(t.extractfile("pilot-1/index.json"))
    assert {e["doc_hash"] for e in index["documents"]} == {"aa", "bb"}
    assert {e["group"] for e in index["documents"]} == {"great"}
    assert sum(n.endswith("/proposal.json") for n in names) == 2
    assert sum(n.endswith("/header.json") for n in names) == 2
    # the writer token is a live credential for that session, not a record
    assert not any(n.endswith("/writer") for n in names)


def test_pull_records_the_latest_sealed_revision(tmp_path, sample_pdf, env):
    data, repo, store, worker = env
    load.load_bundle(bundle(tmp_path, sample_pdf), data, repo)
    drain(worker)
    aa = next(d["id"] for d in repo.list_documents() if d["file_name"] == "aa.pdf")
    for _ in range(2):
        o = store.open(aa)
        store.seal(aa, o["writer"], o["last_seq"], o["proposal"], [])
    pull.pull_campaign("pilot-1", repo, store, tmp_path / "out.tar")
    with tarfile.open(tmp_path / "out.tar") as t:
        index = json.load(t.extractfile("pilot-1/index.json"))
    revs = {e["doc_hash"]: e["sealed_revision"] for e in index["documents"]}
    assert revs == {"aa": 2, "bb": None}


def test_pull_skips_a_drawing_without_a_header_and_counts_it(tmp_path, sample_pdf, env):
    """A drawing still queued (or a damaged record) has no header to name it
    by; pulling mid-campaign is allowed, so it is skipped and counted rather
    than failing the whole pack."""
    data, repo, store, worker = env
    load.load_bundle(bundle(tmp_path, sample_pdf), data, repo)
    drain(worker)
    bb = next(d["id"] for d in repo.list_documents() if d["file_name"] == "bb.pdf")
    (store.root / bb / "header.json").unlink()
    skipped = []
    n = pull.pull_campaign("pilot-1", repo, store, tmp_path / "out.tar", skipped=skipped)
    assert n == 1 and len(skipped) == 1
    with tarfile.open(tmp_path / "out.tar") as t:
        index = json.load(t.extractfile("pilot-1/index.json"))
    assert [e["doc_hash"] for e in index["documents"]] == ["aa"]


def test_pull_keeps_two_drawings_with_one_hash_apart(tmp_path, sample_pdf, env):
    """Two bundles can carry the same doc hash (a re-selection, a rebuilt
    bundle with changed bytes); one archive dir for both would overwrite one
    review with the other inside the tar."""
    data, repo, store, worker = env
    load.load_bundle(bundle(tmp_path, sample_pdf), data, repo)
    load.load_bundle(bundle(tmp_path, sample_pdf, hashes=("aa",), root_name="b2",
                            salt=b"v2"), data, repo)
    drain(worker)
    assert pull.pull_campaign("pilot-1", repo, store, tmp_path / "out.tar") == 3
    with tarfile.open(tmp_path / "out.tar") as t:
        index = json.load(t.extractfile("pilot-1/index.json"))
        names = t.getnames()
    dirs = [e["dir"] for e in index["documents"]]
    assert len(set(dirs)) == 3
    assert sorted(e["doc_hash"] for e in index["documents"]) == ["aa", "aa", "bb"]
    assert sum(n.endswith("/header.json") for n in names) == 3
