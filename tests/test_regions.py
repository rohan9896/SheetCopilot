"""Unit tests for drawing region detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from sheetcopilot.models import DrawingRegion, Segment, Point2D
from sheetcopilot.candidates import is_page_frame_candidate
from sheetcopilot.regions import (
    detect_page_frame,
    run_regions,
)
from sheetcopilot.vector import run_vector_extraction

FIXTURE_PDF = Path("fixtures/wear_plate/input.pdf")


def test_synthetic_page_frame_rejected():
    bbox = (0, 0, 1190, 842)
    assert is_page_frame_candidate(bbox, 1190, 842) is True
    part_bbox = (304, 397, 965, 607)
    assert is_page_frame_candidate(part_bbox, 1190, 842) is False


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_main_view_bbox():
    vector = run_vector_extraction(FIXTURE_PDF)
    regions = run_regions(vector)
    main = next(r for r in regions.regions if r.id == "main_view")
    w = main.bbox[2] - main.bbox[0]
    h = main.bbox[3] - main.bbox[1]
    assert 600 <= w <= 700
    assert 180 <= h <= 230


def test_page_frame_detected_on_border_rectangle():
    segs = [
        Segment(start=Point2D(x=0, y=0), end=Point2D(x=100, y=0), stroke_width=1.5),
        Segment(start=Point2D(x=100, y=0), end=Point2D(x=100, y=80), stroke_width=1.5),
        Segment(start=Point2D(x=100, y=80), end=Point2D(x=0, y=80), stroke_width=1.5),
        Segment(start=Point2D(x=0, y=80), end=Point2D(x=0, y=0), stroke_width=1.5),
    ]
    frame = detect_page_frame(segs, 100, 80)
    assert frame is not None
    assert frame.label == "page_frame"
