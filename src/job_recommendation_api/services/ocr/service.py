"""LLM-vision OCR service.

Adapted from ``microsoft/markitdown``'s ``markitdown-ocr`` plugin (MIT). Only
the PDF subset is vendored here: the upstream package also ships DOCX/PPTX/XLSX
converters and pulls in pandas + pymupdf for them, which this service does not
need. Keeping the dependency tree lean is worth owning a small, stable copy.

The client is an OpenAI-compatible chat client: the service calls
``client.chat.completions.create(...)`` and reads
``response.choices[0].message.content``. ``services.ocr_client`` provides that
surface over OpenRouter using httpx.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, BinaryIO

from markitdown import StreamInfo

_DEFAULT_PROMPT = (
    "Extract all text from this image. "
    "Return ONLY the extracted text, maintaining the original layout and "
    "order. Do not add, fix, infer, complete, or summarize anything. If text "
    "is garbled or unreadable, output it exactly as it appears. Do not add "
    "any commentary or description."
)


@dataclass
class OCRResult:
    """Outcome of a single OCR request."""

    text: str
    confidence: float | None = None
    backend_used: str | None = None
    error: str | None = None


class LLMVisionOCRService:
    """OCR service backed by an OpenAI-compatible vision model.

    Failures never raise: they are reported through ``OCRResult.error`` so the
    calling converter can degrade gracefully instead of crashing the request.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        default_prompt: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.default_prompt = default_prompt or _DEFAULT_PROMPT

    def extract_text(
        self,
        image_stream: BinaryIO,
        prompt: str | None = None,
        stream_info: StreamInfo | None = None,
        **kwargs: Any,
    ) -> OCRResult:
        """Send a single image to the vision model and return its text."""
        if self.client is None:
            return OCRResult(
                text="",
                backend_used="llm_vision",
                error="LLM client not configured",
            )

        try:
            image_stream.seek(0)
            content_type = stream_info.mimetype if stream_info else None
            if not content_type:
                from PIL import Image

                image_stream.seek(0)
                img = Image.open(image_stream)
                fmt = img.format.lower() if img.format else "png"
                content_type = f"image/{fmt}"

            image_stream.seek(0)
            base64_image = base64.b64encode(image_stream.read()).decode("utf-8")
            data_uri = f"data:{content_type};base64,{base64_image}"

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt or self.default_prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
            )
            text = response.choices[0].message.content
            return OCRResult(
                text=text.strip() if text else "",
                backend_used="llm_vision",
            )
        except Exception as exc:  # noqa: BLE001 - report, never raise
            return OCRResult(text="", backend_used="llm_vision", error=str(exc))
        finally:
            image_stream.seek(0)
