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


@pytest.fixture
def diamond_ballooned_pdf(tmp_path):
    """The client's stamping tool draws a DIAMOND from straight lines, not a
    circle. A curve-only shape filter rejects every balloon on the page."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    for num, (x, y) in [(1, (100, 100)), (12, (300, 150)), (7, (480, 300))]:
        r = 11.0
        page.draw_polyline([fitz.Point(x, y - r), fitz.Point(x + r, y),
                            fitz.Point(x, y + r), fitz.Point(x - r, y),
                            fitz.Point(x, y - r)],
                           color=(0, 0, 1), width=1.2)
        page.insert_text(fitz.Point(x - 4, y + 4), str(num), fontsize=9,
                         color=(0, 0, 1))
    # decoys: an oversized diamond and loose dimension text
    page.draw_polyline([fitz.Point(300, 200), fitz.Point(380, 280),
                        fitz.Point(300, 360), fitz.Point(220, 280),
                        fitz.Point(300, 200)], color=(0, 0, 0), width=1.0)
    page.insert_text(fitz.Point(50, 380), "20 +0,1", fontsize=9)
    path = tmp_path / "diamonds.pdf"
    doc.save(path)
    doc.close()
    return path


def test_recovers_diamond_balloons(diamond_ballooned_pdf):
    balloons = recover_balloons(diamond_ballooned_pdf)
    by_num = {b.number: b for b in balloons}
    assert set(by_num) == {1, 12, 7}
    bx, by = by_num[12].center_pt
    assert abs(bx - 300) < 3 and abs(by - 150) < 3


def test_diamond_recovery_ignores_oversized_shapes_and_loose_text(
        diamond_ballooned_pdf):
    assert len(recover_balloons(diamond_ballooned_pdf)) == 3


@pytest.fixture
def outlined_ballooned_pdf(tmp_path):
    """The real stamped drawings: the CAD geometry is flattened to outlines, so
    the only remaining TEXT on the page is the stamped balloon numbers. The
    diamond outline is not a single path object, so shape matching cannot see
    it — but the numbers are still real text."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    for num, (x, y) in [(1, (100, 100)), (12, (300, 150)), (7, (480, 300))]:
        # four independent line segments: no single closed path exists
        r = 11.0
        pts = [(x, y - r), (x + r, y), (x, y + r), (x - r, y), (x, y - r)]
        for a, b in zip(pts, pts[1:]):
            page.draw_line(fitz.Point(*a), fitz.Point(*b), color=(0, 0, 1))
        page.insert_text(fitz.Point(x - 4, y + 4), str(num), fontsize=9)
    path = tmp_path / "outlined.pdf"
    doc.save(path)
    doc.close()
    return path


def test_text_strategy_recovers_balloons_without_a_closed_outline(
        outlined_ballooned_pdf):
    balloons = recover_balloons(outlined_ballooned_pdf, strategy="text")
    by_num = {b.number: b for b in balloons}
    assert set(by_num) == {1, 12, 7}
    bx, by = by_num[12].center_pt
    assert abs(bx - 300) < 6 and abs(by - 150) < 6


def test_auto_falls_back_to_text_when_shapes_find_nothing(
        outlined_ballooned_pdf):
    assert len(recover_balloons(outlined_ballooned_pdf, strategy="auto")) == 3


def test_auto_prefers_shape_matching_when_it_works(ballooned_pdf):
    """With real outlines present, shape matching wins: it rejects the loose
    '20 +0,1' dimension text that a text-only sweep would swallow."""
    assert len(recover_balloons(ballooned_pdf, strategy="auto")) == 3


def test_text_strategy_ignores_implausible_balloon_numbers(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text(fitz.Point(100, 100), "7", fontsize=9)
    page.insert_text(fitz.Point(200, 100), "2026", fontsize=9)   # a year
    page.insert_text(fitz.Point(300, 100), "0", fontsize=9)      # not a balloon
    path = tmp_path / "noise.pdf"
    doc.save(path)
    doc.close()
    assert [b.number for b in recover_balloons(path, strategy="text")] == [7]


def test_text_strategy_can_be_limited_to_expected_numbers(tmp_path):
    """The sheet already lists which balloon numbers exist, so a digit word
    that is not one of them (a title-block number, a revision index) is not a
    balloon. This is a filter on candidates, not an invention of positions."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text(fitz.Point(100, 100), "7", fontsize=9)
    page.insert_text(fitz.Point(200, 100), "42", fontsize=9)   # title-block noise
    path = tmp_path / "expect.pdf"
    doc.save(path)
    doc.close()

    assert sorted(b.number for b in
                  recover_balloons(path, strategy="text")) == [7, 42]
    assert [b.number for b in
            recover_balloons(path, strategy="text", expect={7})] == [7]


def test_recovers_balloons_from_later_pages_when_asked(tmp_path):
    """9 of the 100 delivered drawings run to 2-4 pages. Reading only page 0
    loses every balloon after the first sheet."""
    doc = fitz.open()
    first = doc.new_page(width=600, height=400)
    first.insert_text(fitz.Point(100, 100), "1", fontsize=9)
    second = doc.new_page(width=600, height=400)
    second.insert_text(fitz.Point(120, 120), "2", fontsize=9)
    path = tmp_path / "multipage.pdf"
    doc.save(path)
    doc.close()

    assert [b.number for b in recover_balloons(path, strategy="text")] == [1]
    every = recover_balloons(path, strategy="text", all_pages=True)
    assert sorted(b.number for b in every) == [1, 2]
    assert {b.page for b in every} == {0, 1}


def test_probe_reports_page_count(tmp_path, ballooned_pdf):
    """Recovery only reads page 0; a multi-page drawing would silently lose
    every balloon after the first page."""
    assert probe_pdf(ballooned_pdf)["n_pages"] == 1


def test_shape_report_measures_why_recovery_fails(diamond_ballooned_pdf):
    """Calibration data: how many digit words exist, how many sit inside a
    small symmetric shape, and what the candidate shapes actually look like."""
    from app.eval.balloons import shape_report
    rep = shape_report(diamond_ballooned_pdf)
    assert rep["digit_words"] == 4            # 3 balloons + the decoy "20"
    assert rep["digit_words_in_shape"] == 3   # only the balloons sit inside one
    assert rep["item_kinds"].get("qu", 0) >= 3
    assert any(w >= 3 for w in rep["shape_widths"].values())


def test_probe_reports_annotation_facts(tmp_path):
    """Stamping tools add balloons as ANNOTATIONS, which get_drawings() and
    get_text() never see. The probe must surface them or the encoding question
    cannot be answered."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.add_circle_annot(fitz.Rect(90, 90, 110, 110))
    page.add_freetext_annot(fitz.Rect(95, 95, 115, 115), "7")
    page.add_freetext_annot(fitz.Rect(200, 200, 280, 220), "see note")
    path = tmp_path / "annots.pdf"
    doc.save(path)
    doc.close()

    p = probe_pdf(path)
    assert p["n_annots"] == 3
    assert p["annot_types"].get("FreeText") == 2
    assert p["n_annot_numbers"] == 1


def test_probe_reports_zero_annotations_on_plain_page(tmp_path, ballooned_pdf):
    p = probe_pdf(ballooned_pdf)
    assert p["n_annots"] == 0
    assert p["annot_types"] == {}
    assert p["n_annot_numbers"] == 0


def test_probe_gaps_capped_against_garbage_numbers(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    for num, (x, y) in [(1, (100, 100)), (999999, (300, 150))]:
        page.draw_circle(fitz.Point(x, y), 9.0, color=(0, 0, 1), width=1.5)
        page.insert_text(fitz.Point(x - 8, y + 4), str(num), fontsize=6)
    path = tmp_path / "garbage.pdf"
    doc.save(path)
    doc.close()
    p = probe_pdf(path)
    assert len(p["gaps"]) <= 5000
