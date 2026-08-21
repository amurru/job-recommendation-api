"""PDF -> Markdown converter with LLM-vision OCR for scanned pages.

Adapted from ``microsoft/markitdown``'s ``markitdown-ocr`` plugin (MIT). The
pymupdf fallback for malformed PDFs was dropped to avoid that dependency;
pdfplumber rendering covers the supported inputs.

Behavior, matching upstream:
- pages with embedded images are OCR'd per-image and interleaved with the
  native text by vertical position;
- when a document (or a page) yields no extractable text and an OCR service is
  configured, every page is rendered at 300 DPI and OCR'd in full;
- failures are reported as ``*[...]*`` markers in the markdown, never raised.

Structural bounds (SH-007): page count, page dimensions, and embedded images
per page are capped BEFORE any decoding/OCR work scales with attacker-chosen
numbers (H2). markitdown swallows converter exceptions, so violations set the
``too_complex_reason`` flag (mirroring ``budget_exceeded``) which the
orchestration converter turns into ``DocumentTooComplexError`` after
``convert`` returns.
"""

from __future__ import annotations

import io
import logging
from typing import Any, BinaryIO

import pdfplumber
from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo
from pdfminer.high_level import extract_text as pdfminer_extract_text
from PIL import Image

from job_recommendation_api.services.ocr.service import LLMVisionOCRService

logger = logging.getLogger(__name__)

_NO_TEXT_MARKER = "*[No text could be extracted from this page]*"
_BUDGET_MARKER = "*[OCR skipped: page budget exceeded]*"
# SH-015: static failure marker - upstream exception strings never reach the
# markdown (and therefore never reach an LLM prompt).
_PAGE_ERROR_MARKER = "*[Error processing page]*"

_POINTS_PER_INCH = 72


def _render_png(page: Any, resolution: int) -> BinaryIO:
    """Render a pdfplumber page to an in-memory PNG stream."""
    page_image = page.to_image(resolution=resolution)
    stream = io.BytesIO()
    page_image.original.save(stream, format="PNG")
    stream.seek(0)
    return stream


def _extract_page_images(page: Any, *, max_images: int | None = None) -> list[dict[str, Any]]:
    """Extract embedded images from a page as PNG streams with Y positions.

    At most ``max_images`` images are decoded (SH-007): excess embedded
    images are skipped, not fatal - text-layer extraction still runs.
    """
    images_info: list[dict[str, Any]] = []

    images: list[dict[str, Any]] = []
    if getattr(page, "images", None):
        images = list(page.images)
    if not images and "image" in getattr(page, "objects", {}):
        images = list(page.objects["image"])
    if not images:
        for obj_type, objects in getattr(page, "objects", {}).items():
            if "image" in obj_type.lower() or "xobject" in obj_type.lower():
                images = list(objects)
                break

    for index, img_dict in enumerate(images):
        if max_images is not None and len(images_info) >= max_images:
            break
        img_stream: BinaryIO | None = None
        y_pos = 0
        stream = img_dict.get("stream")
        if stream is not None and hasattr(stream, "get_data"):
            try:
                opened = Image.open(io.BytesIO(stream.get_data()))
                pil_img = opened.convert("RGB") if opened.mode not in ("RGB", "L") else opened
                img_stream = io.BytesIO()
                pil_img.save(img_stream, format="PNG")
                img_stream.seek(0)
                y_pos = img_dict.get("top", 0)
            except Exception:  # noqa: BLE001 - fall through to region render
                img_stream = None

        if img_stream is None:
            x0 = img_dict.get("x0", 0)
            y0 = img_dict.get("top", 0)
            x1 = img_dict.get("x1", 0)
            y1 = img_dict.get("bottom", 0)
            y_pos = y0
            if x1 > x0 and y1 > y0:
                try:
                    cropped = page.within_bbox((x0, y0, x1, y1))
                    img_stream = _render_png(cropped, 150)
                except Exception:  # noqa: BLE001 - skip unrenderable image
                    img_stream = None

        if img_stream is not None:
            images_info.append(
                {
                    "stream": img_stream,
                    "name": f"page_{page.page_number}_img_{index}",
                    "y_pos": y_pos,
                }
            )

    images_info.sort(key=lambda info: int(info["y_pos"]))
    return images_info


class PdfConverterWithOCR(DocumentConverter):
    """PDF converter that OCRs images and scanned pages via LLM vision.

    A fresh instance is constructed per conversion, so the vision-call
    counter, ``budget_exceeded`` flag, and ``too_complex_reason`` flag are
    per-document. Neither condition is enforced by raising inline: markitdown
    catches converter exceptions and silently falls through to the next
    converter, so both flags are read by the orchestration converter after
    ``convert`` returns.
    """

    def __init__(
        self,
        ocr_service: LLMVisionOCRService | None = None,
        max_ocr_pages: int | None = None,
        *,
        max_pdf_pages: int | None = None,
        max_page_inches: float | None = None,
        max_images_per_page: int | None = None,
    ) -> None:
        super().__init__()
        self.ocr_service = ocr_service
        self.max_ocr_pages = max_ocr_pages
        self.max_pdf_pages = max_pdf_pages
        self.max_page_inches = max_page_inches
        self.max_images_per_page = max_images_per_page
        self.vision_calls = 0
        self.budget_exceeded = False
        self.too_complex_reason: str | None = None

    def _budget_available(self) -> bool:
        """True if one more vision call fits the per-document budget."""
        if self.max_ocr_pages is None:
            return True
        if self.vision_calls >= self.max_ocr_pages:
            self.budget_exceeded = True
            return False
        return True

    def _check_document_pages(self, pdf: Any) -> bool:
        """SH-007: reject structurally oversized documents before any
        per-page decode/OCR work. Returns False when a cap is violated
        (``too_complex_reason`` names the violated cap)."""
        if self.max_pdf_pages is not None and len(pdf.pages) > self.max_pdf_pages:
            self.too_complex_reason = f"the maximum allowed number of pages ({self.max_pdf_pages})"
            return False
        max_points = (
            self.max_page_inches * _POINTS_PER_INCH if self.max_page_inches is not None else None
        )
        if max_points is not None:
            for page in pdf.pages:
                if float(page.width) > max_points or float(page.height) > max_points:
                    self.too_complex_reason = (
                        f"the maximum allowed page size of {self.max_page_inches:g} inches"
                    )
                    return False
        return True

    def _ocr_with_budget(
        self,
        ocr_service: LLMVisionOCRService,
        stream: BinaryIO,
        stream_info: Any = None,
    ) -> str:
        """Run one vision call if the budget allows, else return the marker."""
        if not self._budget_available():
            return _BUDGET_MARKER
        self.vision_calls += 1
        result = ocr_service.extract_text(stream, stream_info=stream_info)
        return result.text

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        extension = (stream_info.extension or "").lower()
        mimetype = (stream_info.mimetype or "").lower()
        if extension == ".pdf":
            return True
        return mimetype.startswith("application/pdf") or mimetype.startswith("application/x-pdf")

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        ocr_service: LLMVisionOCRService | None = kwargs.get("ocr_service") or self.ocr_service

        file_stream.seek(0)
        pdf_bytes = io.BytesIO(file_stream.read())

        markdown = ""
        try:
            with pdfplumber.open(pdf_bytes) as pdf:
                if not self._check_document_pages(pdf):
                    # Fail fast on structural violations: no per-page work.
                    return DocumentConverterResult(markdown="")
                markdown = "\n\n".join(
                    self._convert_page(page, pdf_bytes, ocr_service) for page in pdf.pages
                ).strip()
        except Exception:  # noqa: BLE001 - fall back to pdfminer text extraction
            pdf_bytes.seek(0)
            markdown = self._extract_with_pdfminer(pdf_bytes)

        if ocr_service and not markdown.strip() and self.too_complex_reason is None:
            pdf_bytes.seek(0)
            markdown = self._ocr_full_pages(pdf_bytes, ocr_service)

        return DocumentConverterResult(markdown=markdown.strip())

    def _convert_page(
        self,
        page: Any,
        pdf_bytes: io.BytesIO,
        ocr_service: LLMVisionOCRService | None,
    ) -> str:
        parts = [f"\n## Page {page.page_number}\n"]
        images_on_page = (
            _extract_page_images(page, max_images=self.max_images_per_page)
            if ocr_service is not None
            else []
        )

        if images_on_page:
            assert ocr_service is not None
            content_items = self._page_text_lines(page)
            for info in images_on_page:
                text = self._ocr_with_budget(ocr_service, info["stream"])
                if text == _BUDGET_MARKER:
                    parts.append(_BUDGET_MARKER)
                    continue
                if text.strip():
                    content_items.append({"y_pos": info["y_pos"], "type": "image", "text": text})
            content_items.sort(key=lambda item: item["y_pos"])
            for item in content_items:
                if item["type"] == "text":
                    parts.append(item["text"])
                else:
                    parts.append(f"\n\n*[Image OCR]\n{item['text']}\n[End OCR]*\n")
            return "\n".join(parts)

        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
        return "\n".join(parts)

    def _page_text_lines(self, page: Any) -> list[dict[str, Any]]:
        """Extract text lines with their Y positions for interleaving."""
        chars = page.chars
        if not chars:
            text = page.extract_text() or ""
            return [
                {"y_pos": index * 10, "type": "text", "text": line}
                for index, line in enumerate(text.split("\n"))
                if line.strip()
            ]

        lines: list[dict[str, Any]] = []
        current_line: list[Any] = []
        current_y: float | None = None
        for char in sorted(chars, key=lambda c: (c["top"], c["x0"])):
            y = float(char["top"])
            if current_y is None:
                current_y = y
            elif abs(y - current_y) > 2:
                text = "".join(c["text"] for c in current_line)
                if text.strip():
                    lines.append({"y_pos": current_y, "type": "text", "text": text})
                current_line = []
                current_y = y
            current_line.append(char)
        if current_line:
            text = "".join(c["text"] for c in current_line)
            if text.strip():
                lines.append({"y_pos": current_y or 0.0, "type": "text", "text": text})
        return lines

    def _extract_with_pdfminer(self, pdf_bytes: io.BytesIO) -> str:
        try:
            pdf_bytes.seek(0)
            return pdfminer_extract_text(pdf_bytes)
        except Exception:  # noqa: BLE001 - return empty on any upstream failure
            return ""

    def _ocr_full_pages(self, pdf_bytes: io.BytesIO, ocr_service: LLMVisionOCRService) -> str:
        """Render and OCR pages in full (scanned-PDF fallback), bounded by
        the per-document page budget; pages past the budget emit a marker."""
        parts: list[str] = []
        try:
            with pdfplumber.open(pdf_bytes) as pdf:
                if not self._check_document_pages(pdf):
                    return ""
                for page in pdf.pages:
                    parts.append(f"\n## Page {page.page_number}\n")
                    if not self._budget_available():
                        parts.append(_BUDGET_MARKER)
                        continue
                    self.vision_calls += 1
                    try:
                        result = ocr_service.extract_text(
                            _render_png(page, 300),
                            stream_info=StreamInfo(mimetype="image/png"),
                        )
                        if result.text.strip():
                            parts.append(f"*[Image OCR]\n{result.text}\n[End OCR]*")
                        elif result.error:
                            # Static marker: the upstream error string is
                            # logged server-side, never embedded (SH-015).
                            logger.warning(
                                "OCR error on page %s: %s", page.page_number, result.error
                            )
                            parts.append(_NO_TEXT_MARKER)
                        else:
                            parts.append(_NO_TEXT_MARKER)
                    except Exception as exc:  # noqa: BLE001 - per-page guard
                        logger.warning("OCR failed on page %s: %s", page.page_number, exc)
                        parts.append(_PAGE_ERROR_MARKER)
        except Exception:  # noqa: BLE001 - pdfplumber failed to open at all
            return "*[Error: could not process the scanned PDF]*"
        return "\n\n".join(parts).strip()
