"""Unit tests for primitive classification."""

from __future__ import annotations

import math

import pytest

from sheetcopilot.classify import classify_segment
from sheetcopilot.models import (
    DrawingRegion,
    Point2D,
    PrimitiveCategory,
    RegionsResult,
    Segment,
    TextSpan,
)


def _regions(main_bbox: tuple[float, float, float, float] | None = None) -> RegionsResult:
    regions = [
        DrawingRegion(id="page_frame", label="page_frame", bbox=(0, 0, 1200, 850), confidence=0.9),
        DrawingRegion(id="title_block", label="title_block", bbox=(700, 600, 1200, 850), confidence=0.9),
    ]
    if main_bbox:
        regions.append(
            DrawingRegion(id="main_view", label="main_view", bbox=main_bbox, confidence=0.9)
        )
    return RegionsResult(
        page_width_pt=1200,
        page_height_pt=850,
        regions=regions,
        main_view_id="main_view" if main_bbox else None,
    )


def test_tiny_curve_is_annotation_symbol():
    seg = Segment(
        start=Point2D(x=0, y=0),
        end=Point2D(x=7, y=3),
        stroke_width=1.4,
        source_object_type="curve",
        region_id="main_view",
    )
    regions = _regions((100, 100, 900, 500))
    cat = classify_segment(seg, regions, [], 1200, 850)
    assert cat == PrimitiveCategory.ANNOTATION_SYMBOL


def test_thick_main_view_segment_is_manufacturing_candidate():
    seg = Segment(
        start=Point2D(x=200, y=400),
        end=Point2D(x=800, y=400),
        stroke_width=1.4,
        region_id="main_view",
    )
    regions = _regions((100, 100, 900, 500))
    cat = classify_segment(seg, regions, [], 1200, 850)
    assert cat == PrimitiveCategory.MANUFACTURING_CANDIDATE
