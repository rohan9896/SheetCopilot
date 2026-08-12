"""Unit tests for hole feature grouping."""

from __future__ import annotations

import pytest

from sheetcopilot.features import group_concentric_circles, resolve_hole_group
from sheetcopilot.models import Chain, ChainAnalysis, ChainPoint, FittedCircle, Point2D


def _circle_chain(cid: int, cx: float, cy: float, r: float) -> tuple[ChainAnalysis, float, float, float]:
    ca = ChainAnalysis(
        chain=Chain(
            id=cid,
            points=[ChainPoint(x=cx, y=cy)],
            stroke_width=1.4,
            closed=True,
            segment_ids=[f"s{cid}"],
            region_id="main_view",
        ),
        fitted_circle=FittedCircle(center=Point2D(x=cx, y=cy), radius_pt=r, max_deviation_pt=0.1, confidence=0.9),
        is_circular=True,
        bbox=(cx - r, cy - r, cx + r, cy + r),
    )
    return ca, cx, cy, r


def test_concentric_grouping():
    c1 = _circle_chain(1, 100, 200, 10)
    c2 = _circle_chain(2, 101, 201, 19)
    groups = group_concentric_circles([c1, c2], tol_pt=5)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_cut_vs_secondary_by_annotation():
    inner = _circle_chain(1, 100, 200, 10.2)
    outer = _circle_chain(2, 100, 200, 19.3)
    hf = resolve_hole_group([inner, outer], scale_mm_per_pt=0.882, annotated_diameters=[18, 34], hole_idx=0)
    assert hf.through_diameter_mm == pytest.approx(18.0, abs=0.5)
    assert len(hf.secondary_ops) == 1
    assert hf.secondary_ops[0]["outer_diameter_mm"] == pytest.approx(34.0, abs=0.5)
