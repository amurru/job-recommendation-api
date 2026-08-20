"""PDF/image -> Markdown conversion behind a ``DocumentConverter`` Protocol.

The markitdown third-party API is isolated to this module so SDK upgrades are
confined here and tests can inject a fake converter.

Photo resumes arrive either as scanned PDFs (image-only pages) or as plain
image files. Image uploads are wrapped into an in-memory PDF, and a vendored
LLM-vision OCR converter (``services.ocr``) is registered on the MarkItDown
instance so scanned pages are rendered and read even when no text layer
exists.
"""

from __future__ import annotations

import io
from typing import Protocol

from markitdown import MarkItDown, StreamInfo
from PIL import Image

from job_recommendation_api.errors import (
    DocumentConversionError,
    InvalidDocumentError,
)
from job_recommendation_api.services.ocr import (
    LLMVisionOCRService,
    PdfConverterWithOCR,
)
from job_recommendation_api.services.ocr_client import OpenRouterVisionClient

_OCR_FAILURE_MARKERS = (
    "*[No text could be extracted from this page]*",
    "*[OCR error:",
    "*[Error processing page",
    "*[Error: could not process the scanned PDF]*",
)

_OCR_PRIORITY = -1.0  # higher priority than the built-in PdfConverter (0.0)


class DocumentConverter(Protocol):
    """Converts uploaded document bytes into Markdown text."""

    def convert(self, document_bytes: bytes, *, name: str) -> str: ...


def _classify(document_bytes: bytes) -> str | None:
    """Return ``"pdf"``, ``"image"`` or ``None`` from magic bytes."""
    head = document_bytes.lstrip()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG"):
        return "image"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image"
    return None


def _wrap_image_as_pdf(image_bytes: bytes) -> bytes:
    """Embed a photo resume image into a single-page in-memory PDF."""
    try:
        opened = Image.open(io.BytesIO(image_bytes))
        image = opened.convert("RGB") if opened.mode not in ("RGB", "L") else opened
        out = io.BytesIO()
        image.save(out, format="PDF", resolution=300)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001 - normalize unsupported images
        raise InvalidDocumentError("The uploaded image could not be read.") from exc


class MarkItDownConverter:
    """markitdown-backed converter.

    A fresh ``MarkItDown()`` is created per call: the class initializes an
    internal ``requests.Session`` that is not thread-safe, so instances must
    never be shared across threads. The OCR client (an ``httpx.Client``) is
    thread-safe and shared.
    """

    def __init__(
        self,
        *,
        ocr_client: OpenRouterVisionClient | None = None,
        ocr_model: str | None = None,
    ) -> None:
        self._ocr_client = ocr_client
        self._ocr_model = ocr_model

    def convert(self, document_bytes: bytes, *, name: str) -> str:
        if not document_bytes:
            raise InvalidDocumentError("The uploaded file is empty.")
        kind = _classify(document_bytes)
        if kind is None:
            raise InvalidDocumentError("The file is not a PDF or image document.")
        if kind == "image":
            document_bytes = _wrap_image_as_pdf(document_bytes)

        converter = self._build_markitdown()
        try:
            stream = io.BytesIO(document_bytes)
            result = converter.convert_stream(
                stream,
                stream_info=StreamInfo(
                    extension=".pdf",
                    mimetype="application/pdf",
                    filename=name,
                ),
            )
        except DocumentConversionError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize upstream failures
            raise DocumentConversionError("The document could not be converted to text.") from exc

        markdown = result.markdown or ""
        if not markdown.strip():
            raise DocumentConversionError(
                "The document contains no extractable text. If it is a photo or "
                "scanned page, OCR could not read it."
            )
        if self._ocr_client is not None and any(
            marker in markdown for marker in _OCR_FAILURE_MARKERS
        ):
            raise DocumentConversionError("OCR could not extract text from the document.")
        return markdown.strip()

    def _build_markitdown(self) -> MarkItDown:
        """Build a MarkItDown instance, optionally with the vendored OCR converter."""
        if self._ocr_client is None:
            return MarkItDown()
        ocr_service = LLMVisionOCRService(client=self._ocr_client, model=self._ocr_model or "")
        converter = MarkItDown(
            llm_client=self._ocr_client,
            llm_model=self._ocr_model,
        )
        converter.register_converter(
            PdfConverterWithOCR(ocr_service=ocr_service),
            priority=_OCR_PRIORITY,
        )
        return converter
