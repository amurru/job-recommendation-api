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
"""

from __future__ import annotations

import io
from typing import Any, BinaryIO

import pdfplumber
from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo
from pdfminer.high_level import extract_text as pdfminer_extract_text
from PIL import Image

from job_recommendation_api.services.ocr.service import LLMVisionOCRService

_NO_TEXT_MARKER = "*[No text could be extracted from this page]*"


def _render_png(page: Any, resolution: int) -> BinaryIO:
    """Render a pdfplumber page to an in-memory PNG stream."""
    page_image = page.to_image(resolution=resolution)
    stream = io.BytesIO()
    page_image.original.save(stream, format="PNG")
    stream.seek(0)
    return stream


def _extract_page_images(page: Any) -> list[dict[str, Any]]:
    """Extract embedded images from a page as PNG streams with Y positions."""
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
    """PDF converter that OCRs images and scanned pages via LLM vision."""

    def __init__(self, ocr_service: LLMVisionOCRService | None = None) -> None:
        super().__init__()
        self.ocr_service = ocr_service

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
                markdown = "\n\n".join(
                    self._convert_page(page, pdf_bytes, ocr_service) for page in pdf.pages
                ).strip()
        except Exception:  # noqa: BLE001 - fall back to pdfminer text extraction
            pdf_bytes.seek(0)
            markdown = self._extract_with_pdfminer(pdf_bytes)

        if ocr_service and not markdown.strip():
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
        images_on_page = _extract_page_images(page) if ocr_service is not None else []

        if images_on_page:
            assert ocr_service is not None
            content_items = self._page_text_lines(page)
            for info in images_on_page:
                result = ocr_service.extract_text(info["stream"])
                if result.text.strip():
                    content_items.append(
                        {"y_pos": info["y_pos"], "type": "image", "text": result.text}
                    )
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
        """Render every page and OCR it in full (scanned-PDF fallback)."""
        parts: list[str] = []
        try:
            with pdfplumber.open(pdf_bytes) as pdf:
                for page in pdf.pages:
                    parts.append(f"\n## Page {page.page_number}\n")
                    try:
                        result = ocr_service.extract_text(
                            _render_png(page, 300),
                            stream_info=StreamInfo(mimetype="image/png"),
                        )
                        if result.text.strip():
                            parts.append(f"*[Image OCR]\n{result.text}\n[End OCR]*")
                        elif result.error:
                            parts.append(f"*[OCR error: {result.error}]*")
                        else:
                            parts.append(_NO_TEXT_MARKER)
                    except Exception as exc:  # noqa: BLE001 - per-page guard
                        parts.append(f"*[Error processing page {page.page_number}: {exc}]*")
        except Exception:  # noqa: BLE001 - pdfplumber failed to open at all
            return "*[Error: could not process the scanned PDF]*"
        return "\n\n".join(parts).strip()
