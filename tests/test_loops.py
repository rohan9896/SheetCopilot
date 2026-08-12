"""Connectivity-based loop extraction and primitive reconstruction."""

from __future__ import annotations

import math

import pytest

from sheetcopilot.loops import (
    build_loops,
    connected_components,
    extract_part_profile,
    find_circle_features,
    find_closed_loops,
    find_profile_loops,
    loop_inside,
    loop_to_primitives,
    point_in_loop,
    polygon_area,
    split_at_corners,
    trace_component,
)
from sheetcopilot.models import Point2D, Segment

PAGE_W = 1000.0
PAGE_H = 1000.0


def seg(x0: float, y0: float, x1: float, y1: float, width: float = 1.0, sid: str = "") -> Segment:
    return Segment(
        id=sid or f"s_{x0}_{y0}_{x1}_{y1}_{width}",
        start=Point2D(x=x0, y=y0),
        end=Point2D(x=x1, y=y1),
        stroke_width=width,
    )


def square(x0: float, y0: float, size: float, width: float = 1.0) -> list[Segment]:
    x1, y1 = x0 + size, y0 + size
    return [
        seg(x0, y0, x1, y0, width),
        seg(x1, y0, x1, y1, width),
        seg(x1, y1, x0, y1, width),
        seg(x0, y1, x0, y0, width),
    ]


def circle_segments(
    cx: float,
    cy: float,
    r: float,
    n: int = 48,
    width: float = 1.0,
    start_deg: float = 0.0,
    sweep_deg: float = 360.0,
    tag: str = "c",
) -> list[Segment]:
    pts = [
        (
            cx + r * math.cos(math.radians(start_deg + sweep_deg * i / n)),
            cy + r * math.sin(math.radians(start_deg + sweep_deg * i / n)),
        )
        for i in range(n + 1)
    ]
    return [
        seg(*pts[i], *pts[i + 1], width, sid=f"{tag}_{i}")
        for i in range(len(pts) - 1)
    ]


def test_connected_components_separates_disjoint_geometry() -> None:
    segments = square(0, 0, 100) + square(500, 500, 100)
    assert len(connected_components(segments)) == 2


def test_trace_closed_square_returns_closed_loop() -> None:
    points, closed = trace_component(square(0, 0, 100))
    assert closed
    assert polygon_area(points) == pytest.approx(10000.0)


def test_loops_are_ordered_by_enclosed_area() -> None:
    loops = build_loops(square(0, 0, 50) + square(200, 200, 150))
    assert loops[0].area_pt2 > loops[1].area_pt2
    assert loops[0].width_pt == pytest.approx(150.0)


def test_page_frame_loop_is_rejected() -> None:
    frame = square(1, 1, 990)
    part = square(300, 300, 200)
    loops = find_profile_loops(frame + part, PAGE_W, PAGE_H)
    assert len(loops) == 1
    assert loops[0].width_pt == pytest.approx(200.0)


def test_dimension_line_touching_profile_does_not_join_it() -> None:
    """A thinner dimension line sharing a corner must stay out of the profile loop."""
    profile = square(200, 200, 300, width=1.4)
    dimension = [
        seg(200, 200, 200, 80, 0.7),
        seg(200, 80, 500, 80, 0.7),
        seg(500, 80, 500, 200, 0.7),
    ]
    loops = find_profile_loops(profile + dimension, PAGE_W, PAGE_H)
    assert len(loops[0].segments) == 4
    assert loops[0].stroke_width == pytest.approx(1.4)
    assert loops[0].area_pt2 == pytest.approx(90000.0)


def test_centre_mark_at_profile_width_is_not_part_of_the_loop() -> None:
    profile = square(200, 200, 300, width=1.4)
    centre_mark = [seg(330, 350, 370, 350, 1.4), seg(350, 330, 350, 370, 1.4)]
    loops = find_profile_loops(profile + centre_mark, PAGE_W, PAGE_H)
    assert len(loops) == 1
    assert len(loops[0].segments) == 4


def test_closed_circle_becomes_one_circle_primitive() -> None:
    loops = build_loops(circle_segments(500, 500, 40))
    primitives = loop_to_primitives(loops[0], 1.0, "hole")
    assert len(primitives) == 1
    assert primitives[0].type == "circle"
    assert primitives[0].radius_mm == pytest.approx(40.0, abs=0.2)


def test_square_loop_becomes_four_lines_not_a_noisy_polyline() -> None:
    """Collinear runs collapse to single lines instead of per-segment noise."""
    side = [seg(0, 0, 25, 0), seg(25, 0, 50, 0), seg(50, 0, 100, 0)]
    rest = [seg(100, 0, 100, 100), seg(100, 100, 0, 100), seg(0, 100, 0, 0)]
    loops = build_loops(side + rest)
    primitives = loop_to_primitives(loops[0], 1.0, "outer")
    assert [p.type for p in primitives] == ["line"] * 4


def test_split_at_corners_keeps_smooth_run_whole() -> None:
    arc = [(math.cos(t / 40), math.sin(t / 40)) for t in range(30)]
    assert len(split_at_corners(arc, closed=False)) == 1


def test_circle_drawn_as_two_half_arcs_is_reported_once() -> None:
    halves = circle_segments(400, 400, 30, n=24, start_deg=0, sweep_deg=175, tag="a")
    halves += circle_segments(400, 400, 30, n=24, start_deg=185, sweep_deg=170, tag="b")
    features = find_circle_features(halves)
    assert len(features) == 1
    assert features[0].radius_pt == pytest.approx(30.0, abs=0.3)
    assert features[0].coverage_deg > 300


def test_concentric_rings_are_reported_separately() -> None:
    features = find_circle_features(
        circle_segments(400, 400, 10, tag="inner")
        + circle_segments(400, 400, 20, tag="outer")
    )
    assert sorted(round(f.radius_pt) for f in features) == [10, 20]


def test_point_in_loop_and_containment() -> None:
    loops = build_loops(square(100, 100, 400))
    outer = loops[0]
    assert point_in_loop(300, 300, outer)
    assert not point_in_loop(50, 50, outer)

    inner = build_loops(square(200, 200, 100))[0]
    assert loop_inside(inner, outer)
    assert not loop_inside(outer, inner)


def test_extract_part_profile_keeps_only_circles_inside_the_part() -> None:
    profile = square(200, 200, 400, width=1.4)
    inside_hole = circle_segments(400, 400, 20, width=1.4, tag="in")
    outside_circle = circle_segments(800, 800, 20, width=1.4, tag="out")

    result = extract_part_profile(
        profile + inside_hole + outside_circle, PAGE_W, PAGE_H
    )
    assert result is not None
    assert len(result.circles) == 1
    assert result.circles[0].center[0] == pytest.approx(400.0, abs=0.5)


def test_small_holes_survive_the_lower_area_gate() -> None:
    """Holes are far below the profile area gate and need the relaxed threshold."""
    tiny = circle_segments(500, 500, 6)
    assert find_profile_loops(tiny, PAGE_W, PAGE_H) == []
    assert len(find_closed_loops(tiny, PAGE_W, PAGE_H, min_area_ratio=1e-6)) == 1
