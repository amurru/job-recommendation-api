"""PDF -> Markdown conversion behind a ``DocumentConverter`` Protocol.

The markitdown third-party API is isolated to this module so SDK upgrades are
confined here and tests can inject a fake converter.
"""

from __future__ import annotations

import io
from typing import Protocol

from markitdown import MarkItDown, StreamInfo

from job_recommendation_api.errors import (
    DocumentConversionError,
    InvalidDocumentError,
)


class DocumentConverter(Protocol):
    """Converts uploaded document bytes into Markdown text."""

    def convert(self, pdf_bytes: bytes, *, name: str) -> str: ...


class MarkItDownConverter:
    """markitdown-backed converter.

    A fresh ``MarkItDown()`` is created per call: the class initializes an
    internal ``requests.Session`` that is not thread-safe, so instances must
    never be shared across threads.
    """

    def convert(self, pdf_bytes: bytes, *, name: str) -> str:
        if not pdf_bytes:
            raise InvalidDocumentError("The uploaded file is empty.")
        # Loose magic-number check for a PDF header before handing to markitdown.
        if not pdf_bytes.lstrip().startswith(b"%PDF"):
            raise InvalidDocumentError("The file is not a PDF document.")

        converter = MarkItDown()
        try:
            stream = io.BytesIO(pdf_bytes)
            result = converter.convert_stream(
                stream,
                stream_info=StreamInfo(extension=".pdf", mimetype="application/pdf", filename=name),
            )
        except DocumentConversionError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize upstream failures
            raise DocumentConversionError("The document could not be converted to text.") from exc

        markdown = result.markdown
        if not markdown or not markdown.strip():
            raise DocumentConversionError(
                "The PDF contains no extractable text (it may be a scanned image)."
            )
        return markdown.strip()
