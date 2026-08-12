"""DXF export and round-trip validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from sheetcopilot.dxf import export_part_dxf, verify_dxf_roundtrip
from sheetcopilot.models import GeometryPrimitive, HoleClassification, PartDefinition, Point2D


def test_dxf_roundtrip_simple_part(tmp_path: Path):
    part = PartDefinition(
        part_number="ROUNDTRIP-001",
        thickness_mm=10,
        outer_contour=[
            GeometryPrimitive(
                id="l0",
                type="line",
                points=[Point2D(x=0, y=0), Point2D(x=100, y=0)],
            ),
            GeometryPrimitive(
                id="l1",
                type="line",
                points=[Point2D(x=100, y=0), Point2D(x=100, y=50)],
            ),
            GeometryPrimitive(
                id="l2",
                type="line",
                points=[Point2D(x=100, y=50), Point2D(x=0, y=50)],
            ),
            GeometryPrimitive(
                id="l3",
                type="line",
                points=[Point2D(x=0, y=50), Point2D(x=0, y=0)],
            ),
        ],
        internal_features=[
            GeometryPrimitive(
                id="h1",
                type="circle",
                center=Point2D(x=50, y=25),
                radius_mm=9,
            )
        ],
        holes=[
            HoleClassification(
                id="h1",
                center=Point2D(x=50, y=25),
                diameter_mm=18,
                operation="cut",
            )
        ],
        scale_mm_per_pt=1.0,
    )
    dxf_path = tmp_path / "part.dxf"
    export_part_dxf(part, dxf_path)
    passed, notes, counts = verify_dxf_roundtrip(dxf_path, part)
    assert passed, notes
    assert counts.get("CIRCLE", 0) >= 1
