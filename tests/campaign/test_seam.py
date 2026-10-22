"""The campaign's three tools end to end on one synthetic drawing: what the
operator's builder writes is what the loader accepts, what the worker turns
into a review record, and what the pull hands to grading. Each tool has its
own tests; this one catches a fact renamed on one side of a seam only."""
import json
import tarfile

from app.batch import db
from app.batch.paths import DataDir
from app.batch.repo import Repo
from app.batch.worker import Worker
from app.campaign import load, pull
from app.eval.dump import save_dump
from app.eval.models import PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult
from app.review.store import ReviewStore

import campaign_build

H = "1a2b3c4d"


def test_build_load_extract_pull(tmp_path, sample_pdf):
    rows = [Characteristic(pos=1, id="r1", raw_text="20 +0,1 -0,1", nominal="20",
                           kind="dimension", target_region=(40, 40, 120, 70),
                           balloon_xy=(10, 10), confidence=0.995)]
    dump_path = save_dump(
        PredictionDump(doc_id="DOC-1", config=RunConfig(), scale=300 / 72.0,
                       page_rect=(0, 0, 595, 842),
                       result=ExtractionResult(characteristics=rows)),
        tmp_path / "runs" / "r5-control")
    bundle = tmp_path / "bundle"
    campaign_build.build_entry(dump_path, sample_pdf, bundle, campaign="pilot-1",
                               group="great", run="r5-control", doc_hash=H)

    data = DataDir(tmp_path / "data")
    conn = db.connect(data.db_path)
    db.migrate(conn)
    repo, store = Repo(conn), ReviewStore(tmp_path / "reviews")

    def no_gpu(*a):
        raise AssertionError("a campaign drawing must never be extracted")
    worker = Worker(repo, data, store, no_gpu, lambda pdf: {"h": 1})
    try:
        assert load.load_bundle(bundle, data, repo)["loaded"] == 1
        assert worker.run_once() is not None
        [doc] = repo.list_documents()
        assert doc["state"] == "ready"
        assert pull.pull_campaign("pilot-1", repo, store, data, tmp_path / "out.tar") == 1
    finally:
        worker.stop()
        conn.close()

    with tarfile.open(tmp_path / "out.tar") as t:
        index = json.load(t.extractfile("pilot-1/index.json"))
        [entry] = index["documents"]
        header = json.load(t.extractfile(f"pilot-1/{entry['dir']}/header.json"))
    assert entry["doc_hash"] == H and entry["group"] == "great"
    assert index["skipped"] == []
    assert header["campaign"]["source_run"] == "r5-control"
    assert header["campaign"]["render_scale"] == 300 / 72.0
