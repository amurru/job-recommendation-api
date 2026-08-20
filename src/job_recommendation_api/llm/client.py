"""Async LLM client abstraction over the OpenRouter SDK.

The ``LLMClient`` Protocol is prompt-agnostic: it receives fully-built message
lists and a JSON Schema, and does NOT depend on ``services/prompts.py`` (keeps
the ``llm`` layer below ``services``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

import httpx
from openrouter import OpenRouter
from openrouter.components.chatformatjsonobjectconfig import (
    ChatFormatJSONObjectConfig,
)
from openrouter.components.chatformatjsonschemaconfig import (
    ChatFormatJSONSchemaConfig,
)
from openrouter.components.chatjsonschemaconfig import ChatJSONSchemaConfig
from openrouter.errors import BadRequestResponseError, OpenRouterError

from job_recommendation_api.config import Settings
from job_recommendation_api.errors import (
    ConfigurationError,
    LLMError,
    LLMInvalidOutputError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)

Message = dict[str, str]

_RETRIABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


class LLMClient(Protocol):
    """Async, schema-guided LLM completion."""

    async def complete(
        self, messages: list[Message], *, schema: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


def _is_retriable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, OpenRouterError):
        raw = getattr(exc, "raw_response", None)
        status = getattr(raw, "status_code", None)
        return status in _RETRIABLE_STATUS_CODES
    return False


class OpenRouterLLMClient:
    """OpenRouter-backed LLMClient with timeout, retry and typed errors."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: OpenRouter | None = None
        self._max_retries = 2
        self._base_backoff_seconds = 1.0

    async def __aenter__(self) -> OpenRouterLLMClient:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def start(self) -> None:
        """Open the underlying SDK client (called once in lifespan).

        If no API key is configured, the client stays unopened and
        ``complete()`` fails fast with ``ConfigurationError``.
        """
        key = self._settings.openrouter_api_key.get_secret_value().strip()
        if not key:
            logger.warning("OPENROUTER_API_KEY is not set; LLM calls will fail.")
            return
        self._client = await OpenRouter(api_key=key).__aenter__()  # type: ignore[no-untyped-call]

    async def close(self) -> None:
        """Close the underlying SDK client to release HTTP connections."""
        client = self._client
        self._client = None
        if client is not None:
            await client.__aexit__(None, None, None)  # type: ignore[no-untyped-call]

    @property
    def model(self) -> str:
        return self._settings.openrouter_model

    async def complete(self, messages: list[Message], *, schema: dict[str, Any]) -> dict[str, Any]:
        """Send the messages with strict ``json_schema`` output and return a
        parsed dict, retrying transient failures with exponential backoff.
        """
        use_json_schema = True
        attempts = 0
        max_attempts = self._max_retries + 1

        while True:
            attempts += 1
            try:
                content = await self._send_once(messages, schema, use_json_schema)
                return self._parse_content(content)
            except BadRequestResponseError:
                if use_json_schema:
                    logger.warning(
                        "Model rejected json_schema response_format; "
                        "falling back to json_object mode (model=%s)",
                        self.model,
                    )
                    use_json_schema = False
                    attempts = 0
                    continue
                raise LLMError("The model rejected the request.") from None
            except TimeoutError:
                raise LLMTimeoutError("The language model call timed out.") from None
            except ConfigurationError:
                raise
            except LLMError:
                raise
            except Exception as exc:
                if attempts < max_attempts and _is_retriable(exc):
                    delay = self._base_backoff_seconds * (2 ** (attempts - 1))
                    logger.warning(
                        "LLM transient error (attempt %s/%s), retrying in %ss",
                        attempts,
                        max_attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise LLMError("The language model service failed.") from exc

    async def _send_once(
        self,
        messages: list[Message],
        schema: dict[str, Any],
        use_json_schema: bool,
    ) -> str:
        client = self._client
        if client is None:
            raise ConfigurationError("OPENROUTER_API_KEY is not set.")

        if use_json_schema:
            response_format: ChatFormatJSONSchemaConfig | ChatFormatJSONObjectConfig = (
                ChatFormatJSONSchemaConfig(
                    type="json_schema",
                    json_schema=ChatJSONSchemaConfig(
                        name="resume_recommendations",
                        strict=True,
                        schema_=schema,
                    ),
                )
            )
        else:
            response_format = ChatFormatJSONObjectConfig(type="json_object")

        try:
            result: Any = await client.chat.send_async(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                response_format=response_format,
                stream=False,
                temperature=self._settings.llm_temperature,
                max_tokens=self._settings.llm_max_tokens,
                timeout_ms=int(self._settings.llm_timeout_seconds * 1000),
            )
        except TimeoutError:
            raise
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("The language model call timed out.") from exc

        try:
            message = result.choices[0].message
        except (IndexError, AttributeError) as exc:
            raise LLMInvalidOutputError("The model returned an empty response.") from exc

        text = self._extract_text(message.content)
        if not text:
            finish_reason = getattr(result.choices[0], "finish_reason", None)
            raise LLMInvalidOutputError(
                "The model returned an empty response "
                f"(finish_reason={finish_reason!r}); it may have exhausted its token budget."
            )
        return text

    def _extract_text(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                else:
                    text = getattr(part, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return ""

    def _parse_content(self, content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMInvalidOutputError("The model returned malformed JSON.") from exc
        if not isinstance(parsed, dict):
            raise LLMInvalidOutputError("The model returned a non-object JSON response.")
        return parsed
