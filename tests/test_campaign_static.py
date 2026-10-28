"""Static-file contracts of the campaign UI that no DOM test can reach (the
codebase has none): read as text, so a rename or a dropped attribute fails
here instead of on the client's screen."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
MAIN = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles" / "components.css").read_text(encoding="utf-8")


def _fn(src: str, name: str) -> str:
    """The body of a top-level `async function name(` up to the next one."""
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n(async )?function \w+\(", src[start + 1:])
    return src[start: start + 1 + nxt.start()] if nxt else src[start:]


def test_campaign_chrome_is_hidden_until_health_answers():
    """On a cold server /api/health can take a second, and until it answers
    the page did not know it was campaign-only: the work list showed Import
    to the client. The body starts `health-pending`, which hides the same
    controls campaign-only hides, and only a health answer lifts it."""
    assert re.search(r'<body[^>]*class="[^"]*\bhealth-pending\b', HTML)
    assert re.search(r"body\.health-pending\s+\[data-campaign-hide\]\s*\{\s*display:\s*none\s*!important",
                     CSS)
    ping = _fn(MAIN, "pingHealth")
    assert "classList.remove('health-pending')" in ping
    # an unreachable API keeps the chrome hidden and tries again
    assert "setTimeout(pingHealth, 5000)" in ping


def test_an_upload_waits_for_health():
    """Drag-and-drop listens on the whole window, so no hidden button stops
    it: before health answers, the page cannot know uploads are off."""
    assert "health-pending" in _fn(MAIN, "handleFile")


def test_stop_extraction_is_campaign_chrome():
    """Stop cancels a drawing, a route campaign-only 403s."""
    assert re.search(r'<button[^>]*id="ex-stop"[^>]*data-campaign-hide', HTML)


def _js_set(src: str, name: str) -> set:
    m = re.search(rf"export const {name} = new Set\(\[(.*?)\]\)", src, re.S)
    assert m, f"{name} not found"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_the_ui_vocabularies_mirror_the_journal():
    """The server refuses an unknown reason at append, which stalls the
    reviewer's whole journal, and a longer note than NOTE_MAX; the browser
    holds its own copies of all three. tests/js pins the JS side to itself,
    this pins it to Python."""
    from app.review import journal
    reasons = (STATIC / "js" / "reasons.js").read_text(encoding="utf-8")
    campaign = (STATIC / "js" / "campaign.js").read_text(encoding="utf-8")
    assert _js_set(reasons, "EDIT_REASONS") == set(journal.EDIT_REASONS)
    assert _js_set(reasons, "DELETE_REASONS") == set(journal.DELETE_REASONS)
    assert int(re.search(r"export const NOTE_MAX = (\d+);", campaign).group(1)) == journal.NOTE_MAX
    m = re.search(r'<textarea[^>]*id="doc-note"[^>]*maxlength="(\d+)"', HTML) or \
        re.search(r'<textarea[^>]*maxlength="(\d+)"[^>]*id="doc-note"', HTML)
    assert int(m.group(1)) == journal.NOTE_MAX


def test_the_char_type_choices_are_exactly_what_the_parser_can_emit():
    """The Characteristic column offers a closed list (chartypes.js). A type
    the parser never produces would be scored as a mislabel against gold; one
    it does produce but the list lacks could only be typed by hand."""
    from app.pipeline import parser
    src = (STATIC / "js" / "chartypes.js").read_text(encoding="utf-8")
    m = re.search(r"export const CHAR_TYPES = \[(.*?)\]", src, re.S)
    assert m, "CHAR_TYPES not found"
    offered = re.findall(r"'([^']+)'", m.group(1))
    emitted = {parser.DIAMETER, parser.RADIUS, parser.FLATNESS, parser.DISTANCE,
               parser.MATERIAL, parser.NOTE, parser.THEORETICAL, parser.REFERENCE,
               *parser._GDT_SYMBOLS.values()}
    assert set(offered) == emitted
    assert len(offered) == len(set(offered))
