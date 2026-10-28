"""campaign_ops.sh end to end, on synthetic data only (never the client's).

The pieces have their own tests (builder, loader, worker, store, pull, grader:
tests/eval/test_campaign_c2_seam.py runs them in-process). What no test ran
until now is the operator's SCRIPT: the env it exports, the load it does
before serving, the campaign-only app it starts, the pull it packs. The
operator's first real run must not be the first run of that glue."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.eval.anon import Anonymizer
from app.eval.campaign_grade import grade_campaign
from app.eval.campaign_records import read_records
from app.eval.dump import save_dump
from app.eval.models import PredictionDump, RunConfig
from app.models import Characteristic, ExtractionResult
from app.review.journal import net_events
from app.review.replay import replay

import campaign_build

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "campaign_ops.sh"
SALT = "t"
DOC_ID = "DOC-1"

pytestmark = pytest.mark.skipif(
    subprocess.run(["bash", "-c", "true"]).returncode != 0, reason="needs bash")


def _env(tmp_path, **extra):
    """The operator's shell: no CLAUDECODE (the script refuses an agent
    session), an isolated campaign dir and a free port."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env.update(SINDRI_CAMPAIGN_LOCAL=str(tmp_path / "campaign-local"),
               SINDRI_CAMPAIGN_PORT=str(port), SINDRI_DOC_SALT=SALT, **extra)
    return env


def _ops(*args, env, **kw):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, cwd=REPO,
                          capture_output=True, text=True, timeout=120, **kw)


@pytest.fixture
def bundle(tmp_path, sample_pdf):
    rows = [Characteristic(pos=1, id="r1", raw_text="20 +0,1 -0,1", kind="dimension",
                           target_region=(400, 400, 560, 470), balloon_xy=(10, 10),
                           confidence=0.995)]
    dump = save_dump(
        PredictionDump(doc_id=DOC_ID, config=RunConfig(), scale=300 / 72.0,
                       page_rect=(0, 0, 595, 842),
                       result=ExtractionResult(characteristics=rows)),
        tmp_path / "runs" / "r5-control")
    out = tmp_path / "bundle"
    campaign_build.build_entry(dump, sample_pdf, out, campaign="pilot-1", group="great",
                               run="r5-control", doc_hash=Anonymizer(salt=SALT)(DOC_ID))
    return out


def _call(base, method, path, body=None):
    req = urllib.request.Request(
        base + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def _wait(predicate, what, server, timeout=60):
    """Poll until `predicate` is truthy. A server that exited (or never came
    up) fails at once with what the script printed, so a broken script reads
    as its own error and not as a bare timeout."""
    end = time.time() + timeout
    while time.time() < end:
        if server.poll() is not None:
            raise AssertionError(f"server exited {server.returncode} before {what}:\n"
                                 + server.stdout.read())
        try:
            got = predicate()
        except (urllib.error.URLError, ConnectionError, OSError):
            got = None
        if got:
            return got
        time.sleep(0.3)
    server.terminate()
    raise AssertionError(f"timed out waiting for {what}:\n" + server.communicate(timeout=20)[0])


def test_local_up_serves_the_campaign_and_local_pull_packs_the_review(tmp_path, bundle):
    env = _env(tmp_path)
    base = f"http://127.0.0.1:{env['SINDRI_CAMPAIGN_PORT']}"
    server = subprocess.Popen(["bash", str(SCRIPT), "local-up", str(bundle)], env=env,
                              cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True)
    try:
        health = _wait(lambda: _call(base, "GET", "/api/health")[1], "health", server)
        assert health["campaign_only"] is True       # the VPS's switches, locally
        assert _call(base, "POST", "/api/uploads", {})[0] == 403   # writes closed

        def ready():
            _, docs = _call(base, "GET", "/api/documents")
            rows = (docs or {}).get("documents", docs) or []
            return rows if rows and rows[0]["state"] == "ready" else None
        [doc] = _wait(ready, "the loaded drawing to be ready", server)
        assert doc["campaign"] == "pilot-1"

        opened = _call(base, "POST", f"/api/documents/{doc['id']}/open")[1]
        ev = [{"seq": 1, "t_wall": 1_700_000_000_000, "t_ms": 0, "type": "edit_cell",
               "id": "r1", "field": "nominal", "old": opened["rows"][0]["nominal"],
               "new": "21", "reason": "misread_digit"}]
        assert _call(base, "POST", f"/api/session/{doc['id']}/events",
                     {"writer": opened["writer"], "events": ev})[1] == {"contiguous": 1}
        state = replay(opened["rows"], net_events(ev))
        rows = [Characteristic.model_validate(
            {k: v for k, v in r.items() if k in Characteristic.model_fields}
        ).model_dump(mode="json") for r in state.values()]
        code, sealed = _call(base, "POST", f"/api/session/{doc['id']}/seal",
                             {"writer": opened["writer"], "final_seq": 1, "rows": rows,
                              "reviewed_ids": [i for i, r in state.items() if r["reviewed"]]})
        assert code == 200 and sealed["replay_ok"] is True
    finally:
        server.terminate()
        server.wait(timeout=20)

    tar = tmp_path / "pilot-1.tar"
    res = _ops("local-pull", "pilot-1", str(tar), env=env)
    assert res.returncode == 0, res.stderr
    records = read_records(tar)
    report, digest = grade_campaign(records, None, {
        "runs": [{"run": "r5-control", "hashes": [Anonymizer(salt=SALT)(DOC_ID)]}],
        "roles": {Anonymizer(salt=SALT)(DOC_ID): "great"}, "pool": {"great": 1, "problem": 1}})
    assert digest["drawings"]["approved"] == 1 and digest["reconciles"] is True
    assert report["drawings"][0]["rows"][0]["grade"] == "edited"


def test_local_wipe_deletes_only_after_a_yes(tmp_path):
    env = _env(tmp_path)
    data = Path(env["SINDRI_CAMPAIGN_LOCAL"])
    (data / "data").mkdir(parents=True)
    no = _ops("local-wipe", env=env, input="no\n")
    assert no.returncode != 0 and data.exists()
    yes = _ops("local-wipe", env=env, input="yes\n")
    assert yes.returncode == 0 and not data.exists()


def test_an_agent_session_is_refused(tmp_path):
    env = {**_env(tmp_path), "CLAUDECODE": "1"}
    res = _ops("local-wipe", env=env)
    assert res.returncode == 2 and "operator tool" in res.stderr


# --- the move to the VPS, without a VPS -------------------------------------
# ssh and rsync are replaced by stubs that log what they were asked to do (and
# keep what was piped into ssh). That pins the exact commands the operator's
# first real vps-* run will send, which is the part that has never run. Real
# git: `git archive HEAD | ssh ... tar -x` is part of what is under test.

_STUB = """#!/usr/bin/env bash
# {name}: log argv one per line between markers; save stdin if it is a pipe.
{{ echo "--- {name}"; printf '%s\\n' "$@"; }} >> "$STUB_LOG"
if [[ ! -t 0 ]]; then cat >> "$STUB_STDIN.{name}"; fi
"""


@pytest.fixture
def stubs(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ssh", "rsync"):
        f = bin_dir / name
        f.write_text(_STUB.format(name=name))
        f.chmod(0o755)
    env = _env(tmp_path, PATH=f"{bin_dir}:{os.environ['PATH']}",
               STUB_LOG=str(tmp_path / "calls.log"), STUB_STDIN=str(tmp_path / "stdin"))

    def calls():
        log = Path(env["STUB_LOG"])
        text = log.read_text() if log.exists() else ""
        out, cur = [], None
        for line in text.splitlines():
            if line.startswith("--- "):
                cur = [line[4:]]
                out.append(cur)
            else:
                cur.append(line)
        return out
    return env, calls


def test_vps_load_sends_the_bundle_then_loads_it_inside_the_container(
        tmp_path, bundle, stubs):
    env, calls = stubs
    res = _ops("vps-load", "box", "pilot-1", str(bundle) + "/", env=env)
    assert res.returncode == 0, res.stderr
    rsync, ssh = calls()
    # Trailing slash on the source: the bundle's CONTENTS land in import/<name>/,
    # not a nested bundle/ directory the loader would not find.
    assert rsync[:3] == ["rsync", "-a", "--"] and rsync[3] == f"{bundle}/"
    assert rsync[4] == "box:~/sindri/import/pilot-1/"
    assert ssh[1] == "box" and ssh[2].endswith(
        "docker compose exec -T sindri python -m app.campaign.load /import/pilot-1")


def test_vps_load_refuses_a_missing_bundle_before_touching_the_network(
        tmp_path, stubs):
    env, calls = stubs
    res = _ops("vps-load", "box", "pilot-1", str(tmp_path / "nope"), env=env)
    assert res.returncode == 1 and "no such bundle" in res.stderr
    assert calls() == []


def test_vps_pull_packs_remotely_then_fetches_the_tar(tmp_path, stubs):
    env, calls = stubs
    dest = tmp_path / "reviews"
    dest.mkdir()
    res = _ops("vps-pull", "box", "pilot-1", str(dest), env=env)
    assert res.returncode == 0, res.stderr
    ssh, rsync = calls()
    assert ssh[2].endswith(
        "docker compose exec -T sindri python -m app.campaign.pull pilot-1 /export/pilot-1.tar")
    assert rsync[-2:] == ["box:~/sindri/export/pilot-1.tar", f"{dest}/"]


def test_vps_pull_refuses_a_missing_destination_before_touching_the_network(
        tmp_path, stubs):
    env, calls = stubs
    res = _ops("vps-pull", "box", "pilot-1", str(tmp_path / "nope"), env=env)
    assert res.returncode == 1 and "no such directory" in res.stderr
    assert calls() == []


def test_vps_update_ships_the_committed_tree_and_rebuilds_with_its_sha(tmp_path, stubs):
    env, calls = stubs
    res = _ops("vps-update", "box", env=env)
    assert res.returncode == 0, res.stderr
    ship, rebuild = calls()
    assert ship[1] == "box" and "tar -x -C ~/sindri" in ship[2]
    listing = subprocess.run(["tar", "-t", "-f", env["STUB_STDIN"] + ".ssh"],
                             capture_output=True, text=True)
    names = listing.stdout.split()
    assert "campaign_ops.sh" in names and "docker-compose.vps.yml" in names
    assert not any(n.startswith((".git/", "data/", ".env")) for n in names)
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    assert f"SINDRI_VERSION={sha} docker compose up -d --build" in rebuild[2]


def test_vps_init_prepares_dirs_and_the_compose_override_once(tmp_path, stubs):
    env, calls = stubs
    res = _ops("vps-init", "box", env=env)
    assert res.returncode == 0, res.stderr
    prep = calls()[0][2]
    assert "mkdir -p ~/sindri/data ~/sindri/import ~/sindri/export" in prep
    assert "chmod 700" in prep
    # Appended only when absent, so a rerun never duplicates or clobbers .env.
    assert "grep -q '^COMPOSE_FILE='" in prep
    assert "COMPOSE_FILE=docker-compose.yml:docker-compose.vps.yml" in prep
    assert "docker compose ps" in calls()[-1][2]
