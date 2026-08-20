"""OCR client tests (OpenRouter OpenAI-compatible chat completions)."""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import httpx
import pytest
from pydantic import SecretStr

from job_recommendation_api.config import Settings
from job_recommendation_api.errors import ConfigurationError
from job_recommendation_api.services.ocr_client import OpenRouterVisionClient

_BASE = "https://openrouter.ai/api/v1"


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "openrouter_api_key": SecretStr("sk-test"),
        "log_level": "ERROR",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _client_with_handler(handler: Any, settings: Settings) -> OpenRouterVisionClient:
    real_client = httpx.Client(
        base_url=_BASE,
        transport=httpx.MockTransport(handler),
    )
    with mock.patch(
        "job_recommendation_api.services.ocr_client.httpx.Client",
        return_value=real_client,
    ):
        client = OpenRouterVisionClient(settings)
    return client


def test_chat_completions_payload_and_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "extracted text"}}]},
        )

    client = _client_with_handler(handler, _settings(ocr_model="vision/model"))
    try:
        response = client.chat.completions.create(
            model="vision/model",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    ],
                }
            ],
        )
    finally:
        client.close()

    assert captured["url"] == f"{_BASE}/chat/completions"
    assert captured["authorization"] == "Bearer sk-test"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "vision/model"
    content = body["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert response.choices[0].message.content == "extracted text"


def test_http_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "upstream down"})

    client = _client_with_handler(handler, _settings(ocr_model="vision/model"))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.chat.completions.create(
                model="vision/model",
                messages=[{"role": "user", "content": "describe"}],
            )
    finally:
        client.close()


def test_missing_api_key_raises() -> None:
    settings = _settings(openrouter_api_key=SecretStr(""))
    with pytest.raises(ConfigurationError):
        OpenRouterVisionClient(settings)


def test_missing_ocr_model_raises() -> None:
    with pytest.raises(ConfigurationError):
        OpenRouterVisionClient(_settings(ocr_model=""))
