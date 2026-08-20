"""FP-008: OCR page budget in PdfConverterWithOCR."""

from __future__ import annotations

import io
from typing import Any

import pdfplumber

from job_recommendation_api.services.ocr.pdf_converter import PdfConverterWithOCR
from job_recommendation_api.services.ocr.service import LLMVisionOCRService

_BUDGET_MARKER = "*[OCR skipped: page budget exceeded]*"


class _FakeVisionService(LLMVisionOCRService):
    """Counts calls and returns fixed text (never raises)."""

    def __init__(self, pages: int = 20) -> None:
        super().__init__(client=object(), model="fake")
        self.calls = 0
        self._pages = pages

    def extract_text(
        self,
        image_stream: Any,
        prompt: str | None = None,
        stream_info: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.calls += 1
        return type(
            "OCRResult",
            (),
            {"text": f"page text {self.calls}", "error": None},
        )()


def _fake_pdf_bytes(pages: int) -> io.BytesIO:
    """Minimal single-page PDF is enough: budget tests drive _ocr_full_pages
    directly with a fake page list, so only a real pdfplumber-openable file
    is needed for convert()-level tests. Here we test the budget logic."""
    buf = io.BytesIO()
    buf.write(b"%PDF-fake\n")
    return buf


def test_budget_respected_in_full_page_ocr(monkeypatch: Any) -> None:
    from job_recommendation_api.services.ocr import pdf_converter as pc

    service = _FakeVisionService()

    class _FakePage:
        def __init__(self, number: int) -> None:
            self.page_number = number

    class _FakePdf:
        pages = [_FakePage(i) for i in range(1, 6)]

    def fake_open(*args: Any, **kwargs: Any) -> Any:
        class _Ctx:
            def __enter__(self) -> Any:
                return _FakePdf()

            def __exit__(self, *a: object) -> None:
                return None

        return _Ctx()

    monkeypatch.setattr(pdfplumber, "open", fake_open)
    monkeypatch.setattr(pc, "_render_png", lambda page, resolution: io.BytesIO(b"png"))

    converter = PdfConverterWithOCR(ocr_service=service, max_ocr_pages=2)
    markdown = converter._ocr_full_pages(_fake_pdf_bytes(5), service)

    assert service.calls == 2
    assert converter.vision_calls == 2
    assert converter.budget_exceeded is True
    assert markdown.count(_BUDGET_MARKER) == 3
    assert "page text 1" in markdown
    assert "page text 3" not in markdown


def test_no_budget_means_unlimited(monkeypatch: Any) -> None:
    from job_recommendation_api.services.ocr import pdf_converter as pc

    service = _FakeVisionService()

    class _FakePage:
        def __init__(self, number: int) -> None:
            self.page_number = number

    class _FakePdf:
        pages = [_FakePage(i) for i in range(1, 4)]

    def fake_open(*args: Any, **kwargs: Any) -> Any:
        class _Ctx:
            def __enter__(self) -> Any:
                return _FakePdf()

            def __exit__(self, *a: object) -> None:
                return None

        return _Ctx()

    monkeypatch.setattr(pdfplumber, "open", fake_open)
    monkeypatch.setattr(pc, "_render_png", lambda page, resolution: io.BytesIO(b"png"))

    converter = PdfConverterWithOCR(ocr_service=service, max_ocr_pages=None)
    converter._ocr_full_pages(_fake_pdf_bytes(3), service)

    assert service.calls == 3
    assert converter.budget_exceeded is False


def test_budget_never_raises_inline() -> None:
    """Past the budget the converter records the flag; it must not raise
    (markitdown swallows converter exceptions, so inline raises are lost)."""
    service = _FakeVisionService()
    converter = PdfConverterWithOCR(ocr_service=service, max_ocr_pages=0)
    text = converter._ocr_with_budget(service, io.BytesIO(b"png"))
    assert text == _BUDGET_MARKER
    assert converter.budget_exceeded is True
    assert service.calls == 0
