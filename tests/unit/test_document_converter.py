"""ID-005: document converter tests."""

from __future__ import annotations

import io
from collections.abc import Iterator
from unittest import mock

import pytest
from PIL import Image

from job_recommendation_api.config import Settings
from job_recommendation_api.errors import (
    DocumentConversionError,
    InvalidDocumentError,
)
from job_recommendation_api.services.document_converter import MarkItDownConverter
from job_recommendation_api.services.ocr import PdfConverterWithOCR
from job_recommendation_api.services.ocr_client import OpenRouterVisionClient


def _tiny_image_bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="white").save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def ocr_client(test_settings: Settings) -> Iterator[OpenRouterVisionClient]:
    client = OpenRouterVisionClient(test_settings)
    yield client
    client.close()


def test_convert_empty_bytes_raises() -> None:
    with pytest.raises(InvalidDocumentError):
        MarkItDownConverter().convert(b"", name="empty.pdf")


def test_convert_non_document_raises() -> None:
    with pytest.raises(InvalidDocumentError):
        MarkItDownConverter().convert(b"not a pdf or image", name="x.txt")


def test_convert_real_pdf(minimal_pdf_bytes: bytes) -> None:
    markdown = MarkItDownConverter().convert(minimal_pdf_bytes, name="resume.pdf")
    assert "Python" in markdown


def test_convert_png_image_wraps_to_pdf() -> None:
    """A photo resume (PNG) is wrapped into a PDF before conversion."""
    converter = MarkItDownConverter()
    png = _tiny_image_bytes("PNG")
    fake_result = mock.Mock()
    fake_result.markdown = "# Jane\nPython dev\njane@example.com"

    with mock.patch(
        "markitdown.MarkItDown.convert_stream", return_value=fake_result
    ) as fake_convert:
        converter.convert(png, name="photo.png")

    captured = fake_convert.call_args.args[0].getvalue()
    assert captured.lstrip().startswith(b"%PDF")


def test_convert_unsupported_image_format_raises() -> None:
    with pytest.raises(InvalidDocumentError):
        MarkItDownConverter().convert(b"\x00\x01\x02binary garbage", name="x.bin")


def test_convert_empty_result_raises(minimal_pdf_bytes: bytes) -> None:
    """markitdown returning empty markdown -> DocumentConversionError."""
    converter = MarkItDownConverter()
    fake_result = mock.Mock()
    fake_result.markdown = "   "
    with mock.patch("job_recommendation_api.services.document_converter.MarkItDown") as mock_md:
        mock_md.return_value.convert_stream.return_value = fake_result
        with pytest.raises(DocumentConversionError):
            converter.convert(minimal_pdf_bytes, name="resume.pdf")


def test_convert_upstream_error_wrapped(minimal_pdf_bytes: bytes) -> None:
    """Unexpected upstream failures map to DocumentConversionError."""
    converter = MarkItDownConverter()
    with mock.patch("job_recommendation_api.services.document_converter.MarkItDown") as mock_md:
        mock_md.return_value.convert_stream.side_effect = RuntimeError("boom")
        with pytest.raises(DocumentConversionError):
            converter.convert(minimal_pdf_bytes, name="resume.pdf")


def test_convert_with_ocr_registers_pdf_ocr_converter(
    minimal_pdf_bytes: bytes, ocr_client: OpenRouterVisionClient
) -> None:
    """OCR-enabled converter registers the vendored PDF OCR converter."""
    converter = MarkItDownConverter(ocr_client=ocr_client, ocr_model="vision/model")
    with mock.patch("markitdown.MarkItDown.convert_stream") as fake_convert:
        fake_convert.return_value.markdown = "# Jane\nPython dev\njane@example.com"
        converter.convert(minimal_pdf_bytes, name="resume.pdf")

    fake_convert.assert_called_once()
    kwargs = fake_convert.call_args.kwargs
    assert kwargs["stream_info"].extension == ".pdf"


def test_convert_builds_markitdown_with_ocr_kwargs(
    minimal_pdf_bytes: bytes, ocr_client: OpenRouterVisionClient
) -> None:
    converter = MarkItDownConverter(ocr_client=ocr_client, ocr_model="vision/model")
    registered: list[tuple[object, float]] = []

    def fake_register(converter_obj: object, *, priority: float) -> None:
        registered.append((converter_obj, priority))

    with mock.patch("job_recommendation_api.services.document_converter.MarkItDown") as mock_md:
        mock_md.return_value.convert_stream.return_value.markdown = "text"
        mock_md.return_value.register_converter.side_effect = fake_register
        converter.convert(minimal_pdf_bytes, name="resume.pdf")

    assert mock_md.call_args.kwargs["llm_client"] is ocr_client
    assert mock_md.call_args.kwargs["llm_model"] == "vision/model"
    assert any(isinstance(obj, PdfConverterWithOCR) for obj, _ in registered)


def test_convert_without_ocr_no_register_call(minimal_pdf_bytes: bytes) -> None:
    converter = MarkItDownConverter()
    with mock.patch("job_recommendation_api.services.document_converter.MarkItDown") as mock_md:
        mock_md.return_value.convert_stream.return_value.markdown = "text"
        converter.convert(minimal_pdf_bytes, name="resume.pdf")
    mock_md.return_value.register_converter.assert_not_called()


def test_convert_ocr_failure_marker_raises(
    minimal_pdf_bytes: bytes, ocr_client: OpenRouterVisionClient
) -> None:
    """OCR degraded output (per-page failure markers) surfaces as an error."""
    converter = MarkItDownConverter(ocr_client=ocr_client, ocr_model="vision/model")
    with mock.patch("job_recommendation_api.services.document_converter.MarkItDown") as mock_md:
        mock_md.return_value.convert_stream.return_value.markdown = (
            "## Page 1\n*[No text could be extracted from this page]*"
        )
        with pytest.raises(DocumentConversionError):
            converter.convert(minimal_pdf_bytes, name="resume.pdf")


def test_convert_real_pdf_with_ocr_enabled(
    minimal_pdf_bytes: bytes, ocr_client: OpenRouterVisionClient
) -> None:
    """Text PDFs still convert through the OCR-enabled pipeline."""
    converter = MarkItDownConverter(ocr_client=ocr_client, ocr_model="vision/model")
    markdown = converter.convert(minimal_pdf_bytes, name="resume.pdf")
    assert "Python" in markdown


def test_budget_exceeded_flag_raises_ocr_budget_error(
    minimal_pdf_bytes: bytes, ocr_client: OpenRouterVisionClient
) -> None:
    """FP-008: a budget-exceeded flag on the OCR converter surfaces as
    OcrBudgetExceededError - never re-wrapped as conversion_failed."""
    from job_recommendation_api.errors import OcrBudgetExceededError

    converter = MarkItDownConverter(
        ocr_client=ocr_client, ocr_model="vision/model", max_ocr_pages=2
    )
    with mock.patch("job_recommendation_api.services.document_converter.MarkItDown") as mock_md:
        fake_result = mock.Mock()
        fake_result.markdown = "text"
        mock_md.return_value.convert_stream.return_value = fake_result

        # Reach into the registered converter via the build path and flip the
        # flag right before convert_stream runs.
        registered: list[object] = []

        def fake_register(converter_obj: object, *, priority: float) -> None:
            registered.append(converter_obj)

        mock_md.return_value.register_converter.side_effect = fake_register

        def convert_stream_then_flag(*args: object, **kwargs: object) -> object:
            assert isinstance(registered[0], PdfConverterWithOCR)
            registered[0].budget_exceeded = True
            return fake_result

        mock_md.return_value.convert_stream.side_effect = convert_stream_then_flag

        with pytest.raises(OcrBudgetExceededError):
            converter.convert(minimal_pdf_bytes, name="resume.pdf")
