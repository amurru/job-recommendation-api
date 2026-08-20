"""FP-002: OCR extraction prompt contract tests."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from job_recommendation_api.services.ocr.service import (
    _DEFAULT_PROMPT,
    LLMVisionOCRService,
)


class _FakeCompletions:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def create(self, **kwargs: Any) -> Any:
        self._captured.update(kwargs)
        message = type("Message", (), {"content": "extracted text"})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class _FakeChat:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.completions = _FakeCompletions(captured)


class _FakeClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.chat = _FakeChat(captured)


def _png_bytes() -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="white").save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_default_prompt_forbids_fixing_and_inferring() -> None:
    assert _DEFAULT_PROMPT.startswith("Extract all text from this image.")
    assert "Return ONLY the extracted text" in _DEFAULT_PROMPT
    assert "Do not add, fix, infer, complete, or summarize anything." in _DEFAULT_PROMPT
    assert "garbled or unreadable" in _DEFAULT_PROMPT
    assert "Do not add any commentary" in _DEFAULT_PROMPT


def test_default_prompt_embedded_verbatim_in_request() -> None:
    captured: dict[str, Any] = {}
    service = LLMVisionOCRService(client=_FakeClient(captured), model="vision/model")

    result = service.extract_text(_png_bytes())

    assert result.text == "extracted text"
    assert result.error is None
    text_part = captured["messages"][0]["content"][0]
    assert text_part["text"] == _DEFAULT_PROMPT


def test_caller_override_prompt_wins() -> None:
    captured: dict[str, Any] = {}
    service = LLMVisionOCRService(client=_FakeClient(captured), model="vision/model")

    service.extract_text(_png_bytes(), prompt="custom prompt")

    text_part = captured["messages"][0]["content"][0]
    assert text_part["text"] == "custom prompt"
