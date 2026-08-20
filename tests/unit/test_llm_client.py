"""ID-006: LLM client tests."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from job_recommendation_api.config import Settings
from job_recommendation_api.errors import (
    ConfigurationError,
    LLMError,
    LLMInvalidOutputError,
)
from job_recommendation_api.llm.client import (
    OpenRouterLLMClient,
    _is_retriable,
)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "openrouter_api_key": SecretStr("sk-test"),
        "log_level": "ERROR",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_complete_without_start_raises_configuration_error() -> None:
    client = OpenRouterLLMClient(_settings())
    with pytest.raises(ConfigurationError):
        await client.complete([{"role": "user", "content": "hi"}], schema={})


@pytest.mark.asyncio
async def test_start_without_key_leaves_client_unset() -> None:
    client = OpenRouterLLMClient(_settings(openrouter_api_key=SecretStr("")))
    await client.start()
    assert client._client is None
    with pytest.raises(ConfigurationError):
        await client.complete([{"role": "user", "content": "hi"}], schema={})


def test_parse_content_valid_json() -> None:
    client = OpenRouterLLMClient(_settings())
    assert client._parse_content('{"a": 1}') == {"a": 1}


def test_parse_content_invalid_json() -> None:
    client = OpenRouterLLMClient(_settings())
    with pytest.raises(LLMInvalidOutputError):
        client._parse_content("{not json")


def test_parse_content_non_object() -> None:
    client = OpenRouterLLMClient(_settings())
    with pytest.raises(LLMInvalidOutputError):
        client._parse_content("[1, 2, 3]")


def test_extract_text_str() -> None:
    client = OpenRouterLLMClient(_settings())
    assert client._extract_text("hello") == "hello"


def test_extract_text_empty() -> None:
    client = OpenRouterLLMClient(_settings())
    assert client._extract_text(None) == ""


def test_is_retriable_http_status() -> None:
    req = httpx.Request("POST", "http://test")
    resp = httpx.Response(500, request=req)
    exc = httpx.HTTPStatusError("err", request=req, response=resp)
    assert _is_retriable(exc) is False  # HTTPStatusError is not a transport error

    # Connection errors are retriable
    conn_err = httpx.ConnectError("conn", request=req)
    assert _is_retriable(conn_err) is True


@pytest.mark.asyncio
async def test_complete_maps_upstream_failure_to_llm_error() -> None:
    """When the SDK raises a generic exception and retries are exhausted,
    it surfaces as LLMError."""
    from unittest import mock

    client = OpenRouterLLMClient(_settings())
    client._client = mock.AsyncMock()
    client._client.chat.send_async = mock.AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(LLMError):
        await client.complete([{"role": "user", "content": "hi"}], schema={})


@pytest.mark.asyncio
async def test_complete_happy_path() -> None:
    from unittest import mock

    class _Message:
        content = '{"summary": "ok"}'

    class _Choice:
        def __init__(self) -> None:
            self.message = _Message()

    class _Result:
        def __init__(self) -> None:
            self.choices = [_Choice()]

    result = _Result()
    client = OpenRouterLLMClient(_settings())
    client._client = mock.AsyncMock()
    client._client.chat.send_async = mock.AsyncMock(return_value=result)

    out = await client.complete([{"role": "user", "content": "hi"}], schema={})
    assert out == {"summary": "ok"}


@pytest.mark.asyncio
async def test_close_idempotent() -> None:
    client = OpenRouterLLMClient(_settings())
    await client.close()  # should not raise when never started
