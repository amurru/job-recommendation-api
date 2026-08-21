"""SH-007/SH-008: PDF structural caps, image decoding bounds, conversion
deadline. Multi-page PDFs are hand-built (same structure as the conftest
minimal PDF) so no PDF-writing dependency is needed."""

from __future__ import annotations

import asyncio
import io
import time
from typing import Any

import pytest
from PIL import Image
from tests.conftest import FakeLLMClient, FakeProfiler, make_settings

from job_recommendation_api.errors import (
    DocumentConversionError,
    DocumentTooComplexError,
    InvalidDocumentError,
)
from job_recommendation_api.services.document_converter import MarkItDownConverter
from job_recommendation_api.services.extraction_cache import InMemoryExtractionCache
from job_recommendation_api.services.ocr.pdf_converter import (
    PdfConverterWithOCR,
    _extract_page_images,
)
from job_recommendation_api.services.recommendation import RecommendationService


def _build_multipage_pdf(n_pages: int, *, media_box: str = "[0 0 612 792]") -> bytes:
    """Hand-rolled N-page PDF (text layer only) for structural-cap tests."""
    font_num = 3 + 2 * n_pages
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj")
    objects.append(f"2 0 obj<</Type/Pages/Kids[{kids}]/Count {n_pages}>>endobj".encode())
    for i in range(n_pages):
        content = f"BT /F1 12 Tf 72 720 Td (Page {i + 1} Python engineer) Tj ET".encode()
        objects.append(
            f"{3 + 2 * i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox{media_box}"
            f"/Contents {4 + 2 * i} 0 R/Resources<</Font<</F1 {font_num} 0 R>>>>>>endobj".encode()
        )
        objects.append(
            f"{4 + 2 * i} 0 obj<</Length {len(content)}>>stream\n".encode()
            + content
            + b"\nendstream endobj"
        )
    objects.append(
        f"{font_num} 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj".encode()
    )

    body = b"%PDF-1.4\n"
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(body))
        body += obj + b"\n"
    xref_pos = len(body)
    xref = f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()
    xref += f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_pos}\n%%EOF".encode()
    return body + xref


def _png_bytes(width: int = 4, height: int = 4) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="white").save(buf, format="PNG")
    return buf.getvalue()


class _FakeStream:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def get_data(self) -> bytes:
        return self._data


def _fake_image_page(n_images: int) -> Any:
    """Duck-typed pdfplumber page carrying n embedded (tiny) images."""
    return type(
        "FakePage",
        (),
        {
            "images": [
                {"stream": _FakeStream(_png_bytes()), "top": float(i)} for i in range(n_images)
            ],
            "page_number": 1,
        },
    )()


class TestPdfStructuralCaps:
    def test_oversized_page_count_rejected(self) -> None:
        pdf = _build_multipage_pdf(51)
        converter = PdfConverterWithOCR(max_pdf_pages=50, max_page_inches=30.0)
        result = converter.convert(io.BytesIO(pdf), stream_info=Any)  # type: ignore[arg-type]
        assert result.markdown == ""
        assert converter.too_complex_reason is not None
        assert "50" in converter.too_complex_reason

    def test_page_count_cap_checked_before_per_page_work(self) -> None:
        """The 51-page document must be rejected without converting pages."""
        pdf = _build_multipage_pdf(51)
        converter = PdfConverterWithOCR(max_pdf_pages=50, max_page_inches=30.0)
        converter.convert(io.BytesIO(pdf), stream_info=Any)  # type: ignore[arg-type]
        assert converter.vision_calls == 0

    def test_legitimate_multipage_pdf_unaffected(self) -> None:
        pdf = _build_multipage_pdf(3)
        converter = PdfConverterWithOCR(max_pdf_pages=50, max_page_inches=30.0)
        result = converter.convert(io.BytesIO(pdf), stream_info=Any)  # type: ignore[arg-type]
        assert "Page 1" in result.markdown
        assert "Page 3" in result.markdown
        assert converter.too_complex_reason is None

    def test_oversized_page_dimensions_rejected(self) -> None:
        # 3000 points = 41.7 inches > the 30-inch cap.
        pdf = _build_multipage_pdf(1, media_box="[0 0 3000 3000]")
        converter = PdfConverterWithOCR(max_pdf_pages=50, max_page_inches=30.0)
        result = converter.convert(io.BytesIO(pdf), stream_info=Any)  # type: ignore[arg-type]
        assert result.markdown == ""
        assert converter.too_complex_reason is not None
        assert "30" in converter.too_complex_reason

    def test_normal_page_dimensions_pass(self) -> None:
        # 612 x 792 points = 8.5 x 11 inches (letter).
        pdf = _build_multipage_pdf(1, media_box="[0 0 612 792]")
        converter = PdfConverterWithOCR(max_pdf_pages=50, max_page_inches=30.0)
        converter.convert(io.BytesIO(pdf), stream_info=Any)  # type: ignore[arg-type]
        assert converter.too_complex_reason is None

    def test_excess_images_per_page_skipped(self) -> None:
        """A page with 21 embedded images decodes the first 20 and skips
        the rest (non-fatal)."""
        page = _fake_image_page(21)
        images = _extract_page_images(page, max_images=20)
        assert len(images) == 20

    def test_no_image_cap_processes_all(self) -> None:
        page = _fake_image_page(5)
        images = _extract_page_images(page)
        assert len(images) == 5


class TestConverterRaisesTooComplex:
    def test_markitdown_converter_maps_flag_to_error(self) -> None:
        """The too_complex flag surfaces as DocumentTooComplexError (422
        document_too_complex), never re-wrapped as conversion_failed."""

        from job_recommendation_api.services.ocr_client import OpenRouterVisionClient

        settings = make_settings(openrouter_api_key=__import__("pydantic").SecretStr("sk-test"))
        client = OpenRouterVisionClient(settings)
        try:
            converter = MarkItDownConverter(
                ocr_client=client,
                ocr_model="vision/model",
                max_pdf_pages=2,
                max_page_inches=30.0,
            )
            pdf = _build_multipage_pdf(3)
            with pytest.raises(DocumentTooComplexError):
                converter.convert(pdf, name="big.pdf")
        finally:
            client.close()

    def test_error_message_names_the_cap(self) -> None:

        from job_recommendation_api.services.ocr_client import OpenRouterVisionClient

        settings = make_settings(openrouter_api_key=__import__("pydantic").SecretStr("sk-test"))
        client = OpenRouterVisionClient(settings)
        try:
            converter = MarkItDownConverter(
                ocr_client=client,
                ocr_model="vision/model",
                max_pdf_pages=7,
                max_page_inches=30.0,
            )
            pdf = _build_multipage_pdf(9)
            with pytest.raises(DocumentTooComplexError) as excinfo:
                converter.convert(pdf, name="big.pdf")
            assert "7" in str(excinfo.value)
        finally:
            client.close()


class TestImageBounds:
    @pytest.fixture(autouse=True)
    def _reset_pillow_pixel_ceiling(self) -> Any:
        """``Image.MAX_IMAGE_PIXELS`` is process-global; restore a sane
        ceiling after each test so the bomb-guard test cannot leak state."""
        yield
        Image.MAX_IMAGE_PIXELS = 50_000_000

    def test_explicit_max_image_pixels_set(self) -> None:
        MarkItDownConverter(max_image_pixels=123_456)
        assert Image.MAX_IMAGE_PIXELS == 123_456

    def test_decompression_bomb_image_rejected(self) -> None:
        """An image exceeding the explicit pixel ceiling is rejected by
        Pillow's guard (armed at converter construction) and normalized to
        InvalidDocumentError."""
        converter = MarkItDownConverter(max_image_pixels=4)
        # 10x10 = 100 pixels > the 4-pixel ceiling.
        with pytest.raises(InvalidDocumentError):
            converter.convert(_png_bytes(10, 10), name="bomb.png")

    def test_oversized_image_dimension_rejected(self) -> None:
        buf = io.BytesIO()
        Image.new("RGB", (10_001, 10), color="white").save(buf, format="PNG")
        converter = MarkItDownConverter(max_image_dimension=10_000)
        with pytest.raises(InvalidDocumentError, match="dimensions"):
            converter.convert(buf.getvalue(), name="huge.png")

    def test_dimension_at_limit_accepted(self) -> None:
        """A 10_000-px edge is exactly at the cap and wraps without error."""
        from job_recommendation_api.services.document_converter import _wrap_image_as_pdf

        buf = io.BytesIO()
        Image.new("RGB", (10_000, 10), color="white").save(buf, format="PNG")
        wrapped = _wrap_image_as_pdf(buf.getvalue(), max_dimension=10_000)
        assert wrapped.lstrip().startswith(b"%PDF")


class TestConversionDeadline:
    def _service(self, converter: Any, deadline: float, concurrency: int = 1) -> Any:
        from anyio import CapacityLimiter

        return RecommendationService(
            converter,
            FakeLLMClient(),
            model="test/model",
            profiler=FakeProfiler(),
            extraction_cache=InMemoryExtractionCache(max_entries=16, ttl_seconds=60),
            convert_limiter=CapacityLimiter(concurrency),
            convert_deadline_seconds=deadline,
        )

    def test_sleeping_converter_hits_deadline(self) -> None:
        class _SleepyConverter:
            def convert(self, document_bytes: bytes, *, name: str) -> str:
                time.sleep(1.0)
                return "# Jane\nPython developer\njane@example.com"

        service = self._service(_SleepyConverter(), deadline=0.05)
        with pytest.raises(DocumentConversionError, match="timed out"):
            asyncio.run(service.recommend(b"%PDF-fake", name="resume.pdf"))

    def test_deadline_error_maps_to_conversion_failed_code(self) -> None:
        class _SleepyConverter:
            def convert(self, document_bytes: bytes, *, name: str) -> str:
                time.sleep(1.0)
                return "text"

        service = self._service(_SleepyConverter(), deadline=0.05)
        with pytest.raises(DocumentConversionError) as excinfo:
            asyncio.run(service.recommend(b"%PDF-fake", name="resume.pdf"))
        assert excinfo.value.code == "conversion_failed"

    def test_fast_converter_untouched_by_deadline(self) -> None:
        class _FastConverter:
            def convert(self, document_bytes: bytes, *, name: str) -> str:
                return "# Jane\nPython developer\njane@example.com"

        service = self._service(_FastConverter(), deadline=5.0)
        result = asyncio.run(service.recommend(b"%PDF-fake", name="resume.pdf"))
        assert result.analysis.summary

    def test_limiter_token_released_after_deadline(self) -> None:
        """After a deadline expiry the capacity-limiter token must be free
        (statistics show zero borrowed tokens)."""
        from anyio import CapacityLimiter

        class _SleepyConverter:
            def convert(self, document_bytes: bytes, *, name: str) -> str:
                time.sleep(1.0)
                return "text"

        limiter = CapacityLimiter(1)
        service = self._service(_SleepyConverter(), deadline=0.05)
        with pytest.raises(DocumentConversionError):
            asyncio.run(service.recommend(b"%PDF-fake", name="resume.pdf"))
        assert limiter.borrowed_tokens == 0
        assert limiter.available_tokens == 1
