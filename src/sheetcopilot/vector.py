"""Deterministic vector extraction from PDF via pdfplumber."""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from sheetcopilot.models import Point2D, Segment, TextSpan, VectorExtraction


def parse_german_number(text: str) -> float | None:
    """Parse numbers with German decimal comma, e.g. 552,1 -> 552.1."""
    cleaned = text.strip().replace(" ", "")
    if not cleaned:
        return None
    cleaned = re.sub(r"[°n]$", "", cleaned, flags=re.IGNORECASE)
    match = re.fullmatch(r"-?\d+(?:[.,]\d+)?", cleaned)
    if not match:
        return None
    normalized = cleaned.replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def extract_radius_mm(text: str) -> float | None:
    match = re.search(r"R\s*(\d+(?:[.,]\d+)?)", text, re.IGNORECASE)
    if not match:
        return None
    return parse_german_number(match.group(1))


def extract_diameter_mm(text: str) -> float | None:
    match = re.search(r"(?:ø|Ø|dia\.?|diameter)\s*(\d+(?:[.,]\d+)?)", text, re.IGNORECASE)
    if match:
        return parse_german_number(match.group(1))
    if re.search(r"\d+[.,]\d+\s*m\b", text, re.IGNORECASE):
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*n\b", text, re.IGNORECASE)
    if match:
        return parse_german_number(match.group(1))
    return None


def _dash_str(dash: object) -> str | None:
    if dash is None:
        return None
    return str(dash)


def _object_points(obj: dict, page_height: float) -> list[tuple[float, float]]:
    """
    True directed vertices of a pdfplumber path object in y-up page space.

    `pts` preserves stroke direction in (x, top) space. The `x0/y0/x1/y1` keys are
    normalized bounding-box corners, so reading endpoints from them mirrors every
    descending segment onto the opposite diagonal and breaks endpoint chaining.
    """
    pts = obj.get("pts") or []
    if len(pts) >= 2:
        return [(float(px), page_height - float(ptop)) for px, ptop in pts]

    x0, x1 = float(obj["x0"]), float(obj["x1"])
    y0, y1 = float(obj["y0"]), float(obj["y1"])
    return [(x0, y0), (x1, y1)]


def _linewidth(obj: dict) -> float:
    lw = obj.get("linewidth")
    if lw is None:
        lw = obj.get("width", 1.0)
    return float(lw)


def run_vector_extraction(pdf_path: Path) -> VectorExtraction:
    segments: list[Segment] = []
    texts: list[TextSpan] = []
    seg_id = 0

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        width = float(page.width)
        height = float(page.height)

        def emit(obj: dict, kind: str) -> None:
            nonlocal seg_id
            pts = _object_points(obj, height)
            lw = _linewidth(obj)
            dash = _dash_str(obj.get("dash"))
            color = str(obj.get("stroking_color"))
            for i in range(len(pts) - 1):
                (sx, sy), (ex, ey) = pts[i], pts[i + 1]
                segments.append(
                    Segment(
                        id=f"seg_{seg_id}",
                        start=Point2D(x=sx, y=sy),
                        end=Point2D(x=ex, y=ey),
                        stroke_width=lw,
                        page=0,
                        dash=dash,
                        stroking_color=color,
                        source_object_type=kind,
                    )
                )
                seg_id += 1

        for line in page.lines or []:
            emit(line, "line")

        for curve in page.curves or []:
            emit(curve, "curve")

        for rect in page.rects or []:
            x0, y0 = float(rect["x0"]), float(rect["y0"])
            x1, y1 = float(rect["x1"]), float(rect["y1"])
            lw = _linewidth(rect)
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
            for i in range(4):
                segments.append(
                    Segment(
                        id=f"seg_{seg_id}",
                        start=Point2D(x=corners[i][0], y=corners[i][1]),
                        end=Point2D(x=corners[i + 1][0], y=corners[i + 1][1]),
                        stroke_width=lw,
                        page=0,
                        dash=_dash_str(rect.get("dash")),
                        stroking_color=str(rect.get("stroking_color")),
                        source_object_type="rect",
                    )
                )
                seg_id += 1

        # Text is reported in top-based coordinates; store it y-up so text and
        # geometry share one space.
        for word in page.extract_words(use_text_flow=True, keep_blank_chars=False) or []:
            texts.append(
                TextSpan(
                    text=word["text"],
                    x0=float(word["x0"]),
                    y0=height - float(word["bottom"]),
                    x1=float(word["x1"]),
                    y1=height - float(word["top"]),
                    page=0,
                )
            )

    return VectorExtraction(
        segments=segments,
        texts=texts,
        page_width_pt=width,
        page_height_pt=height,
    )


def collect_annotated_dimensions(texts: list[TextSpan]) -> tuple[list[float], list[float]]:
    """Return (linear_mm_values, radius_mm_values) parsed from drawing text."""
    linear: list[float] = []
    radii: list[float] = []

    for span in texts:
        t = span.text.strip()
        if not t:
            continue
        r = extract_radius_mm(t)
        if r is not None:
            radii.append(r)
            continue
        if re.search(r"^\d:\d", t) or re.search(r"^\d{2}\.\d{2}\.\d{4}", t):
            continue
        if re.search(r"^[A-Z]{2,}\s", t):
            continue
        val = parse_german_number(t)
        if val is not None and 1 <= val <= 10000:
            linear.append(val)

    return linear, radii
