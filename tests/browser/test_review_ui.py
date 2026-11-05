"""Real-browser checks for review-screen behaviour no node test can reach (H8).

viewer.js and the reason card only misbehave inside a layout engine, so these
drive Chromium against a live campaign-only server over a synthetic bundle
(the repo's sample drawing, never client data). They SKIP, not fail, where
Playwright or a Chromium build is absent: the dev machine has the cached
build, CI does not. Run them with an interpreter that has `playwright`
installed (`pip install playwright`; set SINDRI_CHROMIUM to the browser binary
if it is not under ~/.cache/ms-playwright).

Each test uses its OWN rows: an edit or a drag is journaled and survives a
reload, so a shared row would start the next test from a moved state.
"""
import glob
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import campaign_build  # noqa: E402
from app.batch import db  # noqa: E402
from app.batch.paths import DataDir  # noqa: E402
from app.batch.repo import Repo  # noqa: E402
from app.campaign import load  # noqa: E402
from app.eval.dump import save_dump  # noqa: E402
from app.eval.models import PredictionDump, RunConfig  # noqa: E402
from app.models import Characteristic, ExtractionResult  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
N_ROWS = 8


def _chromium():
    forced = os.environ.get("SINDRI_CHROMIUM")
    if forced:
        return forced if Path(forced).is_file() else None
    found = sorted(glob.glob(str(Path.home() / ".cache/ms-playwright/chromium-*"
                                 "/chrome-linux64/chrome")))
    return found[-1] if found else None


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    chrome = _chromium()
    if chrome is None:
        pytest.skip("no Chromium build (set SINDRI_CHROMIUM)")
    work = tmp_path_factory.mktemp("browser")
    rows = [Characteristic(pos=i + 1, id=f"r{i + 1}", raw_text=str(10 + i), nominal=str(10 + i),
                           kind="dimension",
                           target_region=(300 + 220 * i, 1500, 480 + 220 * i, 1560),
                           balloon_xy=(330 + 220 * i, 1450), confidence=0.995)
            for i in range(N_ROWS)]
    dump = save_dump(PredictionDump(doc_id="DOC-1", config=RunConfig(), scale=300 / 72.0,
                                    page_rect=(0, 0, 842, 595),
                                    result=ExtractionResult(characteristics=rows)),
                     work / "runs" / "r5-control")
    sample = ROOT / "tests" / "fixtures" / ("sample" + ".pdf")
    bundle = work / "bundle"
    campaign_build.build_entry(dump, sample, bundle, campaign="pilot-1", group="great",
                               run="r5-control", doc_hash="aaaaaaaa")
    data = DataDir(work / "data")
    conn = db.connect(data.db_path)
    db.migrate(conn)
    load.load_bundle(bundle, data, Repo(conn))
    conn.close()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "SINDRI_DATA_DIR": str(data.root), "SINDRI_CAMPAIGN_ONLY": "1",
           "SINDRI_RETENTION_DAYS": "0", "SINDRI_MIN_FREE_MB": "0",
           "OCR_BACKEND": "tesseract", "SINDRI_DOC_SALT": "browser-tests"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port",
                             str(port), "--log-level", "warning"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    base = f"http://127.0.0.1:{port}"
    end = time.time() + 60
    while True:
        if proc.poll() is not None:
            raise AssertionError("server exited:\n" + proc.stdout.read())
        try:
            urllib.request.urlopen(base + "/api/health", timeout=2)
            break
        except OSError:
            if time.time() > end:
                proc.kill()
                raise
            time.sleep(0.3)
    yield base, chrome
    proc.terminate()
    proc.wait(10)


@pytest.fixture
def page(server):
    base, chrome = server
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        pg = browser.new_page(viewport={"width": 1500, "height": 900})
        doc = pg.request.get(f"{base}/api/documents").json()["documents"][0]["id"]
        pg.goto(f"{base}/#/doc/{doc}")
        pg.wait_for_selector('.marker[data-id="r1"]', timeout=30000)
        pg.wait_for_timeout(800)
        yield pg
        browser.close()


def _center(page, row):
    b = page.locator(f'.marker[data-id="{row}"]').bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def test_a_dragged_balloon_stays_under_the_pointer_through_a_wheel_zoom(page):
    """Zoom re-renders every marker from state, and the one being dragged is
    only in its new place in the DOM, so it used to jump back to where it was
    stored until the button was released (H8)."""
    x0, y0 = _center(page, "r1")
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(700, 500, steps=8)
    page.mouse.wheel(0, -600)
    page.wait_for_timeout(300)
    x, y = _center(page, "r1")
    page.mouse.up()
    assert abs(x - 700) <= 3 and abs(y - 500) <= 3, (x, y)


def test_the_first_click_after_enter_focuses_the_next_cell(page):
    """The reason card stays open after Enter and sits right under the edited
    cell, over the NEXT row's cells: the first click landed on the card and
    only the second reached the cell (H8). The card now lets clicks through
    except on its chips and its close button."""
    cell = lambda r: page.locator(f'td[data-id="{r}"][data-field="nominal"]')
    cell("r2").click()
    page.keyboard.press("Control+a")
    page.keyboard.type("77")
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    assert page.locator(".reason-popover").count() == 1     # the card really is open
    cell("r3").click(timeout=3000)
    focused = page.evaluate("() => { const e = document.activeElement;"
                            " return [e.dataset.id || null, e.dataset.field || null]; }")
    assert focused == ["r3", "nominal"]


def test_the_cards_chips_still_take_clicks(page):
    """Letting clicks through the card must not make its chips unreachable."""
    page.locator('td[data-id="r4"][data-field="nominal"]').click()
    page.keyboard.press("Control+a")
    page.keyboard.type("55")
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    page.locator(".reason-popover .chip").first.click(timeout=3000)
    page.wait_for_timeout(300)
    assert page.locator(".reason-popover").count() == 0     # a pick closes the card
