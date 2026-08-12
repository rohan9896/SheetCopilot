"""PDF intake: classify vector vs raster and capture page metadata."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from sheetcopilot.models import IntakeResult


def run_intake(pdf_path: Path) -> IntakeResult:
    notes: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        if page_count == 0:
            raise ValueError("PDF has no pages")
        if page_count > 1:
            notes.append(f"Multi-page PDF ({page_count} pages); prototype uses page 0 only")

        page = pdf.pages[0]
        width = float(page.width)
        height = float(page.height)

        lines = page.lines or []
        curves = page.curves or []
        rects = page.rects or []
        texts = page.extract_words() or []

        is_vector = len(lines) + len(curves) + len(rects) > 50
        has_text = len(texts) > 0

        if not is_vector:
            notes.append("Low vector content; may be scanned raster PDF")
        if not has_text:
            notes.append("No extractable text found on page 0")

        return IntakeResult(
            pdf_path=str(pdf_path.resolve()),
            page_count=page_count,
            is_vector=is_vector,
            has_text=has_text,
            page_width_pt=width,
            page_height_pt=height,
            notes=notes,
        )
