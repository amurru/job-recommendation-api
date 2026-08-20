"""ID-005: document converter tests."""

from __future__ import annotations

from unittest import mock

import pytest

from job_recommendation_api.errors import (
    DocumentConversionError,
    InvalidDocumentError,
)
from job_recommendation_api.services.document_converter import MarkItDownConverter


def test_convert_empty_bytes_raises() -> None:
    with pytest.raises(InvalidDocumentError):
        MarkItDownConverter().convert(b"", name="empty.pdf")


def test_convert_non_pdf_raises() -> None:
    with pytest.raises(InvalidDocumentError):
        MarkItDownConverter().convert(b"not a pdf", name="x.txt")


def test_convert_real_pdf(minimal_pdf_bytes: bytes) -> None:
    markdown = MarkItDownConverter().convert(minimal_pdf_bytes, name="resume.pdf")
    assert "Python" in markdown


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
