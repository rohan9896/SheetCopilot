"""Coordinate extraction: directed endpoints in a single y-up space."""

from __future__ import annotations

import math
from pathlib import Path

import pdfplumber
import pytest

from sheetcopilot.loops import extract_part_profile
from sheetcopilot.vector import run_vector_extraction

FIXTURE_PDF = Path("fixtures/wear_plate/input.pdf")
pytestmark = pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")


@pytest.fixture(scope="module")
def vector():
    return run_vector_extraction(FIXTURE_PDF)


def test_line_endpoints_are_directed_not_bbox_corners(vector) -> None:
    """
    pdfplumber reports x0/y0/x1/y1 as a normalized bounding box.

    Reading endpoints from those corners mirrors every descending segment onto
    the opposite diagonal, which silently breaks endpoint chaining, so the
    directed vertices from `pts` must be used instead.
    """
    with pdfplumber.open(FIXTURE_PDF) as pdf:
        page = pdf.pages[0]
        height = page.height
        descending = [
            ln
            for ln in page.lines
            if ln.get("pts")
            and len(ln["pts"]) == 2
            and ln["pts"][0][0] > ln["pts"][1][0]
            and abs(ln["x1"] - ln["x0"]) > 1
            and abs(ln["y1"] - ln["y0"]) > 0.1
        ]
    assert descending, "fixture should contain right-to-left sloped lines"

    line = descending[0]
    (ax, atop), (bx, btop) = line["pts"]
    expected = {(round(ax, 3), round(height - atop, 3)), (round(bx, 3), round(height - btop, 3))}
    naive = {(round(line["x0"], 3), round(line["y0"], 3)), (round(line["x1"], 3), round(line["y1"], 3))}
    assert expected != naive, "fixture line must actually distinguish the two readings"

    match = [
        s
        for s in vector.segments
        if {(round(s.start.x, 3), round(s.start.y, 3)), (round(s.end.x, 3), round(s.end.y, 3))}
        == expected
    ]
    assert match, "extracted segment should use the directed endpoints"


def test_curves_and_lines_share_one_vertical_orientation(vector) -> None:
    """Curves are reported top-based like lines and must not end up mirrored."""
    with pdfplumber.open(FIXTURE_PDF) as pdf:
        page = pdf.pages[0]
        height = page.height
        curve = next(c for c in page.curves if len(c.get("pts") or []) >= 2)
        first = curve["pts"][0]

    expected_y = height - first[1]
    assert any(
        math.isclose(s.start.x, first[0], abs_tol=1e-6)
        and math.isclose(s.start.y, expected_y, abs_tol=1e-6)
        for s in vector.segments
    )


def test_text_is_stored_in_the_same_y_up_space_as_geometry(vector) -> None:
    """The title block sits at the bottom of the sheet, so its text has low y."""
    title_text = [
        t for t in vector.texts if "S235" in t.text or "KHO" in t.text or "HUMBOLDT" in t.text
    ]
    assert title_text
    assert all(t.y0 < vector.page_height_pt * 0.5 for t in title_text)
    assert all(t.y1 > t.y0 for t in vector.texts)


def test_profile_chains_into_one_closed_loop(vector) -> None:
    """With directed endpoints the part outline joins up instead of fragmenting."""
    profile = extract_part_profile(
        vector.segments, vector.page_width_pt, vector.page_height_pt
    )
    assert profile is not None
    assert profile.outer.closed
    assert len(profile.outer.segments) == 59

    width_mm = profile.outer.width_pt * 0.882152
    height_mm = profile.outer.height_pt * 0.882152
    assert width_mm == pytest.approx(583.3, abs=1.0)
    assert height_mm == pytest.approx(185.0, abs=1.0)


def test_every_profile_vertex_has_exactly_two_neighbours(vector) -> None:
    """A clean closed contour has no dangling ends and no branches."""
    from collections import Counter

    profile = extract_part_profile(
        vector.segments, vector.page_width_pt, vector.page_height_pt
    )
    degree: Counter[tuple[float, float]] = Counter()
    for s in profile.outer.segments:
        degree[(round(s.start.x, 2), round(s.start.y, 2))] += 1
        degree[(round(s.end.x, 2), round(s.end.y, 2))] += 1
    assert set(degree.values()) == {2}
