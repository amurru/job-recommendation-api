"""Vendored ``markitdown-ocr`` subset: LLM-vision OCR for PDFs."""

from job_recommendation_api.services.ocr.pdf_converter import PdfConverterWithOCR
from job_recommendation_api.services.ocr.service import LLMVisionOCRService, OCRResult

__all__ = ["LLMVisionOCRService", "OCRResult", "PdfConverterWithOCR"]
