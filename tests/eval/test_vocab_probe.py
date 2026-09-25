"""A closed-vocabulary probe: which PUBLIC symbols and words occur in client text.

The gdt review found 4 profile-tolerance rows a parser change would free, and
the change needs two facts no count carried: which glyph the reader wrote for
the profile frame, and which label word tells profile of a line from profile
of a surface. The operator chose (2026-09-25) to get them without any client
text reaching an agent: test membership of a FIXED list of GD&T glyphs and
ISO 1101 terms, and report only list members. By construction nothing outside
the list can appear in the output -- the one thing reported about the rest is
a count of rows holding an unlisted symbol, so an inconclusive probe says so.
"""
from app.eval.vocab_probe import (glyph_set, has_unlisted_symbol, word_set,
                                  LABEL_WORDS, TRANSCRIPTION_WORDS)


def test_glyphs_are_found_by_codepoint_including_look_alikes():
    assert glyph_set("⌒ 0,1 A") == {"U+2312 ARC"}
    assert glyph_set("◠0,1 A B") == {"U+25E0 UPPER HALF CIRCLE"}
    assert glyph_set("0,1 A") == set()


def test_ordinary_tolerance_characters_are_not_glyphs():
    """Digits, letters, the diameter sign and tolerance signs are the VALUES of
    a callout, never evidence of its characteristic."""
    assert glyph_set("Ø0,4 ±0,1 +0,2 -0,1 A") == set()


def test_label_words_are_casefolded_and_stripped_of_punctuation():
    assert word_set("Linienform, 0,1 zu A", LABEL_WORDS) == {"linienform"}
    assert word_set("FLÄCHENPROFIL (Profil)", LABEL_WORDS) == {
        "flächenprofil", "profil"}
    assert word_set("Flaechenform", LABEL_WORDS) == {"flaechenform"}


def test_only_listed_words_are_ever_returned():
    assert word_set("Geheimwort 0,1 Linienform", LABEL_WORDS) == {"linienform"}


def test_transcription_words_cover_a_characteristic_written_out():
    assert word_set("Profile 0.1 A", TRANSCRIPTION_WORDS) == {"profile"}


def test_an_unlisted_symbol_is_counted_not_named():
    assert has_unlisted_symbol("★ 0,1") is True
    assert has_unlisted_symbol("⌒ 0,1 A") is False
    assert has_unlisted_symbol("± 0,1 ° Ø") is False


# --- what stands before the value ---------------------------------------------
#
# First run on the real rows: no listed glyph, no listed word, and no symbol-
# class character at all in any of the 8 transcriptions -- yet the operator saw
# a symbol on every one. So it is written with letter- or value-class
# characters. A frame's symbol precedes its tolerance value, so the probe looks
# at the characters BEFORE the first digit: listed look-alikes by name, all
# else by Unicode category only, never the character.

from app.eval.vocab_probe import prefix_signature  # noqa: E402


def test_a_listed_look_alike_before_the_value_is_named():
    assert prefix_signature("Ø0,4 A") == (
        "U+00D8 LATIN CAPITAL LETTER O WITH STROKE")
    assert prefix_signature("⌒ 0,1 A") == "U+2312 ARC"


def test_an_unlisted_character_is_reported_by_category_only():
    assert prefix_signature("Xy 0,1") == "Lu · Ll"


def test_no_characters_before_the_value():
    assert prefix_signature("0,1 A B") == "(no prefix)"
    assert prefix_signature("") == "(no prefix)"


def test_a_long_prefix_is_capped_so_a_word_cannot_be_spelled_out():
    """Four tokens at most: enough to see a symbol, too few to rebuild a word
    from category runs and the few letters on the look-alike list."""
    sig = prefix_signature("Kontrolle 0,1")
    assert sig.count(" · ") == 4 and sig.endswith(" · …")
