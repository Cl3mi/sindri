from app.pipeline.ocr import vlm_backend


def test_gdt_prompt_exists_and_is_frame_aware():
    p = vlm_backend._GDT_PROMPT
    assert "feature control frame" in p.lower()
    assert "datum" in p.lower()
    assert "comma" in p.lower()


def test_notes_block_prompt_requests_json_array():
    from app.pipeline.ocr.vlm_backend import _NOTES_PROMPT
    p = _NOTES_PROMPT
    assert "general-notes" in p.lower()
    assert "JSON array" in p            # structured output, not tab-delimited
    assert '"pos"' in p
    assert "comma as the decimal separator" in p.lower()
    assert "no prose" in p.lower()


def test_title_prompt_requests_json_label_value():
    p = vlm_backend._TITLE_PROMPT
    assert "title block" in p.lower()
    assert '"label"' in p and '"value"' in p
    # caption can be above OR below the value (the two-layout requirement)
    assert "above" in p.lower() and "below" in p.lower()
