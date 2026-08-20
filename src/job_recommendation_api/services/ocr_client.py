"""Minimal OpenAI-compatible chat client for markitdown OCR vision calls.

The vendored OCR service talks to ``client.chat.completions.create(model=...,
messages=[...])`` and reads ``response.choices[0].message.content``. This
module provides exactly that surface on top of OpenRouter's OpenAI-compatible
endpoint using ``httpx``, so the ``openai`` SDK is not a dependency.

It is a sync client used only inside the converter threadpool; the async
analysis LLM client (``llm.client``) is a separate concern.
"""

from __future__ import annotations

from typing import Any

import httpx

from job_recommendation_api.config import Settings
from job_recommendation_api.errors import ConfigurationError

_BASE_URL = "https://openrouter.ai/api/v1"


class OCRMessage:
    """Duck-typed ``choices[0].message``."""

    def __init__(self, content: str) -> None:
        self.content = content


class OCRChoice:
    """Duck-typed ``choices[0]``."""

    def __init__(self, message: OCRMessage) -> None:
        self.message = message


class OCRResponse:
    """Duck-typed chat completion response."""

    def __init__(self, choices: list[OCRChoice]) -> None:
        self.choices = choices


class OCRCompletions:
    """Duck-typed ``chat.completions``."""

    def __init__(self, client: OpenRouterVisionClient) -> None:
        self._client = client

    def create(self, **kwargs: Any) -> OCRResponse:
        return self._client._create_completion(**kwargs)


class OCRChat:
    """Duck-typed ``chat`` attribute."""

    def __init__(self, client: OpenRouterVisionClient) -> None:
        self.completions = OCRCompletions(client)


class OpenRouterVisionClient:
    """OpenAI-compatible chat client backed by OpenRouter."""

    def __init__(self, settings: Settings) -> None:
        key = settings.openrouter_api_key.get_secret_value().strip()
        if not key:
            raise ConfigurationError("OPENROUTER_API_KEY is not set; OCR is unavailable.")
        self._model = settings.ocr_model
        if not self._model:
            raise ConfigurationError("OCR_MODEL is not set; OCR is unavailable.")
        self._http = httpx.Client(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
        )
        self._headers = {"Authorization": f"Bearer {key}"}
        self.chat = OCRChat(self)

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._http.close()

    def _create_completion(self, **kwargs: Any) -> OCRResponse:
        payload: dict[str, Any] = {
            "model": kwargs.get("model") or self._model,
            "messages": kwargs.get("messages", []),
        }
        response = self._http.post(
            "/chat/completions",
            json=payload,
            headers=self._headers,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return OCRResponse([])
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        return OCRResponse([OCRChoice(OCRMessage(content))])
