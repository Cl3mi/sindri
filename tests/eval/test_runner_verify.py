"""`runner verify` end to end on stubs: selects the split's dumps, renders each
page, writes verdict-carrying dumps to --out, resumes, and isolates a failing
document the way predict does."""
import json

from PIL import Image

from app.eval import runner, verify as vm
from app.eval.dump import load_dump, save_dump
from tests.eval.test_verify import SCALE, StubVerifier, _dump


def _setup(tmp_path, monkeypatch, answer=0.2):
    src = tmp_path / "src"
    save_dump(_dump(), src)
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "D.pdf").write_bytes(b"%PDF-stub")
    stub = StubVerifier(answer)
    monkeypatch.setattr(vm, "get_backend", lambda: stub)
    monkeypatch.setattr(vm, "render_for_verify",
                        lambda pdf, dpi, work: (Image.new("RGB", (4200, 4200),
                                                          "white"), SCALE))
    return src, pdfs, stub


def _verify(tmp_path, src, pdfs, *extra):
    return runner.main(["verify", "--run", str(src), "--pdfs", str(pdfs),
                        "--out", str(tmp_path / "out"), *extra])


def test_verify_writes_dumps_with_verdicts(tmp_path, monkeypatch, capsys):
    src, pdfs, stub = _setup(tmp_path, monkeypatch)
    assert _verify(tmp_path, src, pdfs) == 0
    out = load_dump(tmp_path / "out" / "D.pred.json")
    assert {c.pos for c in out.result.characteristics
            if c.verifier_p is not None} == {1, 6}
    summary = json.loads(capsys.readouterr().out)
    assert summary["verified"] == 1 and summary["asked"] == 2


def test_verify_resumes(tmp_path, monkeypatch):
    src, pdfs, stub = _setup(tmp_path, monkeypatch)
    assert _verify(tmp_path, src, pdfs) == 0
    stub.calls.clear()
    assert _verify(tmp_path, src, pdfs) == 0
    assert stub.calls == []


def test_a_failing_document_costs_that_document_only(tmp_path, monkeypatch,
                                                      capsys):
    src, pdfs, stub = _setup(tmp_path, monkeypatch)
    def boom(pdf, dpi, work):
        raise RuntimeError("cannot render /client/secret/path")
    monkeypatch.setattr(vm, "render_for_verify", boom)
    assert _verify(tmp_path, src, pdfs) == 1
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "secret" not in out
