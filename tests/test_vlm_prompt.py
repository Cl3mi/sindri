from app.pipeline.ocr import vlm_backend


def test_gdt_prompt_exists_and_is_frame_aware():
    p = vlm_backend._GDT_PROMPT
    assert "feature control frame" in p.lower()
    assert "datum" in p.lower()
    assert "comma" in p.lower()


def test_notes_block_prompt_is_constrained_and_tab_separated():
    from app.pipeline.ocr.vlm_backend import _NOTES_PROMPT
    p = _NOTES_PROMPT.lower()
    assert "general-notes" in p
    assert "\\t" in p          # the prompt instructs tab-separated output
    assert "comma as the decimal separator" in p
    assert "no prose" in p
