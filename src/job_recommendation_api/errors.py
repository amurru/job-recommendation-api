"""Domain exception hierarchy.

Transport-agnostic: these carry a stable ``code`` string and a human-readable
``detail`` but no HTTP semantics. The mapping to HTTP happens once in
``api/errors.py``.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application domain errors."""

    code: str = "app_error"
    default_message: str = "An application error occurred."
    status_code: int = 500

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.default_message)
        self.detail = detail or self.default_message


class InvalidDocumentError(AppError):
    """The submitted document is not a readable PDF or image."""

    code = "invalid_document"
    default_message = "The file is not a valid PDF or image document."
    status_code = 400


class DocumentConversionError(AppError):
    """The PDF converted to no usable text."""

    code = "conversion_failed"
    default_message = "The document could not be converted to usable text."
    status_code = 422


class NotAResumeError(AppError):
    """The document converted, but does not look like a resume."""

    code = "not_a_resume"
    default_message = "The uploaded document does not appear to be a resume."
    status_code = 422


class OcrBudgetExceededError(AppError):
    """The document needs more OCR pages/calls than the budget allows."""

    code = "ocr_budget_exceeded"
    default_message = "The document exceeds the OCR page budget."
    status_code = 422


class DocumentTooLargeError(AppError):
    """The upload exceeds the configured size cap."""

    code = "document_too_large"
    default_message = "The uploaded file exceeds the maximum allowed size."
    status_code = 413


class UnsupportedMediaTypeError(AppError):
    """The upload is not a PDF or image document."""

    code = "unsupported_media_type"
    default_message = "Only PDF and image documents are supported."
    status_code = 415


class LLMError(AppError):
    """An upstream OpenRouter / model failure."""

    code = "llm_error"
    default_message = "The language model service failed."
    status_code = 502


class LLMTimeoutError(LLMError):
    """The LLM call exceeded the configured timeout."""

    code = "llm_timeout"
    default_message = "The language model call timed out."
    status_code = 504


class LLMInvalidOutputError(LLMError):
    """The model returned malformed or non-schema JSON."""

    code = "llm_invalid_output"
    default_message = "The model returned an invalid response."
    status_code = 422


class ConfigurationError(AppError):
    """The server is misconfigured."""

    code = "configuration_error"
    default_message = "Server configuration error."
    status_code = 500


class UnauthorizedError(AppError):
    """Missing/invalid credentials (SH-002). Maps to 401 with
    ``WWW-Authenticate: Bearer``."""

    code = "unauthorized"
    default_message = "A valid API key is required."
    status_code = 401


class RateLimitedError(AppError):
    """The identity exceeded its request budget (SH-005). Maps to 429 with
    ``Retry-After`` plus the ``X-RateLimit-*`` header set."""

    code = "rate_limited"
    default_message = "Rate limit exceeded."
    status_code = 429

    def __init__(
        self,
        detail: str | None = None,
        *,
        retry_after_seconds: int = 0,
        limit: int = 0,
        remaining: int = 0,
        reset_epoch: float = 0.0,
    ) -> None:
        super().__init__(detail)
        self.retry_after_seconds = retry_after_seconds
        self.limit = limit
        self.remaining = remaining
        self.reset_epoch = reset_epoch


class DocumentTooComplexError(AppError):
    """The document exceeds a structural cap (SH-007): pages, page size, or
    embedded-image count. Checked before any decoding or OCR work."""

    code = "document_too_complex"
    default_message = "The document exceeds the allowed structural limits."
    status_code = 422
