import fitz
import pytest

from app.eval.balloons import recover_balloons, probe_pdf


@pytest.fixture
def ballooned_pdf(tmp_path):
    """A minimal vector 'client' page: three circled numbers + decoy content."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    for num, (x, y) in [(1, (100, 100)), (2, (300, 150)), (12, (500, 300))]:
        page.draw_circle(fitz.Point(x, y), 9.0, color=(0, 0, 1), width=1.5)
        page.insert_text(fitz.Point(x - 5, y + 4), str(num), fontsize=10)
    # decoys: a big circle (not a balloon), loose text, a rectangle
    page.draw_circle(fitz.Point(300, 300), 60.0, color=(0, 0, 0), width=1.0)
    page.insert_text(fitz.Point(50, 350), "20 +0,1", fontsize=10)
    page.draw_rect(fitz.Rect(10, 10, 590, 390), color=(0, 0, 0), width=0.5)
    path = tmp_path / "client.pdf"
    doc.save(path)
    doc.close()
    return path


def test_recovers_all_numbered_balloons(ballooned_pdf):
    balloons = recover_balloons(ballooned_pdf)
    by_num = {b.number: b for b in balloons}
    assert set(by_num) == {1, 2, 12}
    bx, by = by_num[1].center_pt
    assert abs(bx - 100) < 3 and abs(by - 100) < 3


def test_ignores_oversized_circles_and_loose_text(ballooned_pdf):
    balloons = recover_balloons(ballooned_pdf)
    assert len(balloons) == 3            # decoy circle + '20 +0,1' not recovered


def test_probe_reports_encoding_facts(ballooned_pdf):
    p = probe_pdf(ballooned_pdf)
    assert p["n_balloons"] == 3
    assert p["n_circles"] >= 3
    assert p["has_images"] is False
    assert p["numbers"] == [1, 2, 12]
    assert p["duplicate_numbers"] == []
