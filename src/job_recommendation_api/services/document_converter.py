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

import hashlib
import io
from typing import Protocol

from markitdown import MarkItDown, StreamInfo
from PIL import Image

from job_recommendation_api.errors import (
    DocumentConversionError,
    DocumentTooComplexError,
    InvalidDocumentError,
    OcrBudgetExceededError,
)
from job_recommendation_api.services.ocr import (
    LLMVisionOCRService,
    PdfConverterWithOCR,
)
from job_recommendation_api.services.ocr_client import OpenRouterVisionClient

# Bumped when converter, OCR prompt, profile prompt, or profile schema
# semantics change so cached extractions invalidate safely.
EXTRACTION_VERSION = "2"

_OCR_BUDGET_MARKER = "*[OCR skipped: page budget exceeded]*"

_OCR_FAILURE_MARKERS = (
    "*[No text could be extracted from this page]*",
    "*[OCR error:",
    "*[Error processing page",
    "*[Error: could not process the scanned PDF]*",
)

_OCR_PRIORITY = -1.0  # higher priority than the built-in PdfConverter (0.0)


def document_hash(document_bytes: bytes) -> str:
    """SHA-256 hex digest of the ORIGINAL uploaded bytes (before any
    image->PDF wrapping) so identical uploads always share a cache key."""
    return hashlib.sha256(document_bytes).hexdigest()


def cache_key(document_bytes: bytes) -> str:
    """Versioned extraction-cache key for the uploaded document."""
    return f"{document_hash(document_bytes)}:v{EXTRACTION_VERSION}"


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


def _wrap_image_as_pdf(image_bytes: bytes, *, max_dimension: int | None = None) -> bytes:
    """Embed a photo resume image into a single-page in-memory PDF.

    SH-008: images wider/taller than ``max_dimension`` px are rejected
    before decoding scales with attacker-chosen dimensions. The Pillow
    decompression-bomb ceiling (``Image.MAX_IMAGE_PIXELS``) is set
    explicitly at converter construction, never left at the library default.
    """
    try:
        opened = Image.open(io.BytesIO(image_bytes))
        if max_dimension is not None and (
            opened.width > max_dimension or opened.height > max_dimension
        ):
            raise InvalidDocumentError(
                "The uploaded image exceeds the maximum allowed dimensions "
                f"of {max_dimension} pixels."
            )
        image = opened.convert("RGB") if opened.mode not in ("RGB", "L") else opened
        out = io.BytesIO()
        image.save(out, format="PDF", resolution=300)
        return out.getvalue()
    except InvalidDocumentError:
        raise
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
        max_ocr_pages: int | None = None,
        max_pdf_pages: int | None = None,
        max_page_inches: float | None = None,
        max_images_per_page: int | None = None,
        max_image_pixels: int | None = None,
        max_image_dimension: int | None = None,
    ) -> None:
        self._ocr_client = ocr_client
        self._ocr_model = ocr_model
        self._max_ocr_pages = max_ocr_pages
        self._max_pdf_pages = max_pdf_pages
        self._max_page_inches = max_page_inches
        self._max_images_per_page = max_images_per_page
        # SH-008: explicit decompression-bomb ceiling, set once at
        # construction (Pillow's default ~179M pixels is too generous).
        if max_image_pixels is not None:
            Image.MAX_IMAGE_PIXELS = max_image_pixels
        self._max_image_dimension = max_image_dimension

    def convert(self, document_bytes: bytes, *, name: str) -> str:
        if not document_bytes:
            raise InvalidDocumentError("The uploaded file is empty.")
        kind = _classify(document_bytes)
        if kind is None:
            raise InvalidDocumentError("The file is not a PDF or image document.")
        if kind == "image":
            document_bytes = _wrap_image_as_pdf(
                document_bytes, max_dimension=self._max_image_dimension
            )

        converter, ocr_converter = self._build_markitdown()
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
        if ocr_converter is not None:
            # Fail-loud structural violations (SH-007): checked before the
            # generic conversion-failed path so they map to 422
            # document_too_complex, never re-wrapped as conversion_failed.
            if ocr_converter.too_complex_reason is not None:
                raise DocumentTooComplexError(
                    f"The document exceeds {ocr_converter.too_complex_reason}."
                )
            if ocr_converter.budget_exceeded:
                # Fail-loud past the page budget: the generic AppError handler
                # maps this to 422 ocr_budget_exceeded. Never raised inline in
                # the markitdown plugin (markitdown swallows converter errors).
                raise OcrBudgetExceededError(
                    "The document requires more OCR pages than the configured budget allows."
                )
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

    def _build_markitdown(self) -> tuple[MarkItDown, PdfConverterWithOCR | None]:
        """Build a MarkItDown instance, optionally with the vendored OCR converter.

        Returns the instance and the registered OCR converter (or None) so
        ``convert`` can read the per-document budget/complexity flags after
        conversion.
        """
        if self._ocr_client is None:
            return MarkItDown(), None
        ocr_service = LLMVisionOCRService(client=self._ocr_client, model=self._ocr_model or "")
        converter = MarkItDown(
            llm_client=self._ocr_client,
            llm_model=self._ocr_model,
        )
        ocr_converter = PdfConverterWithOCR(
            ocr_service=ocr_service,
            max_ocr_pages=self._max_ocr_pages,
            max_pdf_pages=self._max_pdf_pages,
            max_page_inches=self._max_page_inches,
            max_images_per_page=self._max_images_per_page,
        )
        converter.register_converter(ocr_converter, priority=_OCR_PRIORITY)
        return converter, ocr_converter
