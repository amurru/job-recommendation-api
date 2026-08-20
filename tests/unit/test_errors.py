"""ID-004: domain error hierarchy tests."""

from __future__ import annotations

import pytest

from job_recommendation_api.errors import (
    AppError,
    ConfigurationError,
    DocumentConversionError,
    DocumentTooLargeError,
    InvalidDocumentError,
    LLMError,
    LLMInvalidOutputError,
    LLMTimeoutError,
    UnsupportedMediaTypeError,
)


def test_app_error_defaults() -> None:
    err = AppError()
    assert err.code == "app_error"
    assert err.status_code == 500
    assert err.detail == "An application error occurred."


def test_app_error_custom_detail() -> None:
    err = AppError("custom detail")
    assert err.detail == "custom detail"
    assert str(err) == "custom detail"


@pytest.mark.parametrize(
    "cls,code,status",
    [
        (InvalidDocumentError, "invalid_document", 400),
        (DocumentConversionError, "conversion_failed", 422),
        (DocumentTooLargeError, "document_too_large", 413),
        (UnsupportedMediaTypeError, "unsupported_media_type", 415),
        (LLMError, "llm_error", 502),
        (LLMTimeoutError, "llm_timeout", 504),
        (LLMInvalidOutputError, "llm_invalid_output", 422),
        (ConfigurationError, "configuration_error", 500),
    ],
)
def test_error_codes_and_status(cls: type[AppError], code: str, status: int) -> None:
    err = cls()
    assert err.code == code
    assert err.status_code == status
    assert isinstance(err, AppError)


def test_all_errors_subclass_app_error() -> None:
    classes = [
        InvalidDocumentError,
        DocumentConversionError,
        DocumentTooLargeError,
        UnsupportedMediaTypeError,
        LLMError,
        LLMTimeoutError,
        LLMInvalidOutputError,
        ConfigurationError,
    ]
    for cls in classes:
        assert issubclass(cls, AppError)
