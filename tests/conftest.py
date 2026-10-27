from pathlib import Path
import shutil
import sys
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """Every test gets its own data and review dir. app.main builds its
    Runtime lazily from SINDRI_DATA_DIR / SINDRI_REVIEW_DIR, so without this a
    developer with those exported (e.g. for the real app) would have ordinary
    tests write synthetic drawings and review records into a real -- possibly
    client-data -- directory. Looked up via sys.modules so a module that never
    touches app.main doesn't pay to load the OCR backend."""
    monkeypatch.setenv("SINDRI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SINDRI_REVIEW_DIR", str(tmp_path / "data" / "reviews"))
    # No disk reserve in tests: a developer's /tmp can have less free space
    # than the deployment default (2048 MB), and every upload test would then
    # fail with 507. The reserve itself is tested with injected free space.
    monkeypatch.setenv("SINDRI_MIN_FREE_MB", "0")
    # Auth is opt-in; an ambient SINDRI_ACCESS_TOKEN in a developer's shell
    # must not gate the ~1940 existing tests that never set it themselves.
    monkeypatch.delenv("SINDRI_ACCESS_TOKEN", raising=False)
    # The runner's output checks (H1) consult the protected-roots file. A test
    # must never depend on -- or read -- this machine's real one, so by
    # default every test runs in the no-roots mode the GPU container and CI
    # use; a test about roots points this at its own synthetic file. The
    # once-per-process "no protected roots" notice is pre-silenced so it
    # cannot land in whichever test happens to trigger it first.
    # An EMPTY file, not a missing one: an explicit override that names no
    # file fails closed (review._protected_roots).
    no_roots = tmp_path / "no-protected-roots-in-tests"
    no_roots.write_text("")
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(no_roots))
    monkeypatch.setattr("app.eval.outputs._NO_ROOTS_ANNOUNCED", True)
    mod = sys.modules.get("app.main")
    if mod is not None:
        monkeypatch.setattr(mod, "_RUNTIME", None)
        # A lifespan test leaves the app shut down; the next test must not
        # inherit that.
        monkeypatch.setattr(mod, "_SHUT_DOWN", False)
    yield
    # Looked up again: a test that imported app.main for the first time
    # built its runtime after the lookup above, and it must be closed too.
    mod = sys.modules.get("app.main")
    if mod is not None and mod._RUNTIME is not None:
        mod._RUNTIME.close()
        mod._RUNTIME = None
        mod._SHUT_DOWN = False

@pytest.fixture(scope="session", autouse=True)
def ensure_sample_pdf():
    FIXTURES.mkdir(exist_ok=True)
    target = FIXTURES / "sample.pdf"
    if not target.exists():
        root_pdf = Path(__file__).parents[1] / "sample.pdf"
        shutil.copy(root_pdf, target)

@pytest.fixture
def sample_pdf():
    return FIXTURES / "sample.pdf"


from app.pipeline.detect import Detection
from app.pipeline.ocr.base import OcrResult


class StubVLMBackend:
    """Test double for the VLM backend: returns canned detections (tile-local)
    and a canned transcription. Has detect_regions, so extract() treats it as a
    detection-capable backend."""

    def __init__(self, detections=None, text="1,2 +0,1 -0,1", confidence=0.9,
                 gdt_text="⊕ Ø0.1 A"):
        self._detections = detections or []
        self._text = text
        self._confidence = confidence
        self._gdt_text = gdt_text

    def detect_regions(self, image):
        return [Detection(box=d.box, kind=d.kind, conf=d.conf)
                for d in self._detections]

    def read_region(self, image):
        return OcrResult(text=self._text, confidence=self._confidence)

    def read_region_gdt(self, image):
        return OcrResult(text=self._gdt_text, confidence=self._confidence)
