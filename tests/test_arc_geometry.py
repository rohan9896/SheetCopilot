"""Arcs must be treated as swept segments, never as full circles."""

from __future__ import annotations

import math

import pytest

from sheetcopilot.dxf import _arc_bounds
from sheetcopilot.models import GeometryPrimitive, PartDefinition, Point2D
from sheetcopilot.nest import part_to_polygon
from sheetcopilot.reconstruct import _compute_bbox_mm


def shallow_arc(
    cx: float = 0.0,
    cy: float = 3000.0,
    r: float = 3000.0,
    start: float = -95.0,
    end: float = -85.0,
) -> GeometryPrimitive:
    return GeometryPrimitive(
        id="arc",
        type="arc",
        center=Point2D(x=cx, y=cy),
        radius_mm=r,
        start_angle_deg=start,
        end_angle_deg=end,
        points=[
            Point2D(x=cx + r * math.cos(math.radians(start)), y=cy + r * math.sin(math.radians(start))),
            Point2D(x=cx + r * math.cos(math.radians(end)), y=cy + r * math.sin(math.radians(end))),
        ],
    )


def test_bbox_uses_swept_extent_not_full_circle() -> None:
    bbox = _compute_bbox_mm([shallow_arc()])
    assert bbox is not None
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    assert width == pytest.approx(523.0, abs=5.0)
    assert height == pytest.approx(11.4, abs=1.0)


def test_bbox_includes_axis_crossing_within_the_sweep() -> None:
    """The lowest point of a downward arc is at 270 degrees, not at an endpoint."""
    bbox = _compute_bbox_mm([shallow_arc()])
    assert bbox[1] == pytest.approx(0.0, abs=1e-6)


def test_arc_bounds_of_full_sweep_covers_whole_circle() -> None:
    points = _arc_bounds(0.0, 0.0, 5.0, 0.0, 360.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    assert max(xs) - min(xs) == pytest.approx(10.0)
    assert max(ys) - min(ys) == pytest.approx(10.0)


def _line(x0: float, y0: float, x1: float, y1: float) -> GeometryPrimitive:
    return GeometryPrimitive(
        id=f"l{x0}{y0}{x1}{y1}",
        type="line",
        points=[Point2D(x=x0, y=y0), Point2D(x=x1, y=y1)],
    )


R, CY = 3000.0, -2900.0
START, END = 88.0, 92.0
RIGHT = (R * math.cos(math.radians(START)), CY + R * math.sin(math.radians(START)))
LEFT = (R * math.cos(math.radians(END)), CY + R * math.sin(math.radians(END)))

# Always stored counter-clockwise from START to END, as DXF requires.
TOP_ARC = GeometryPrimitive(
    id="arc",
    type="arc",
    center=Point2D(x=0.0, y=CY),
    radius_mm=R,
    start_angle_deg=START,
    end_angle_deg=END,
)


def _walk_ccw() -> PartDefinition:
    """Contour walked in the same rotational direction the arc is stored in."""
    return PartDefinition(
        outer_contour=[
            _line(LEFT[0], -50.0, RIGHT[0], -50.0),
            _line(RIGHT[0], -50.0, RIGHT[0], RIGHT[1]),
            TOP_ARC,
            _line(LEFT[0], LEFT[1], LEFT[0], -50.0),
        ]
    )


def _walk_cw() -> PartDefinition:
    """Same ring walked the other way, so the arc is met at its end point."""
    return PartDefinition(
        outer_contour=[
            _line(LEFT[0], -50.0, LEFT[0], LEFT[1]),
            TOP_ARC,
            _line(RIGHT[0], RIGHT[1], RIGHT[0], -50.0),
            _line(RIGHT[0], -50.0, LEFT[0], -50.0),
        ]
    )


def test_polygon_area_is_independent_of_walk_direction() -> None:
    """
    Arcs are stored counter-clockwise for DXF, which may oppose the contour walk.

    Sampling one in the stored direction regardless folds the ring into a bow
    tie, so the traversal must decide which way to sample it.
    """
    ccw = part_to_polygon(_walk_ccw())
    cw = part_to_polygon(_walk_cw())

    assert ccw.is_valid and cw.is_valid
    assert cw.area == pytest.approx(ccw.area, rel=1e-6)


def test_polygon_keeps_the_arc_bulge() -> None:
    poly = part_to_polygon(_walk_cw())
    minx, miny, maxx, maxy = poly.bounds
    assert maxy == pytest.approx(100.0, abs=0.5)
    assert poly.area == pytest.approx((maxx - minx) * (maxy - miny), rel=0.15)
