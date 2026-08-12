"""Tests for nesting axis mapping, sheet slicing, and verification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from sheetcopilot.models import (
    HoleClassification,
    LLMSemanticResult,
    NestingPlacement,
    NestingResult,
    PartDefinition,
    Point2D,
    TitleBlockExtraction,
)
from sheetcopilot.nest import (
    SpyrrowNestingEngine,
    get_nesting_engine,
    grid_capacity_per_sheet,
    map_strip_to_sheets,
    normalized_part_polygon,
    placement_geometry,
    verify_nesting,
    _PlacedFootprint,
)
from sheetcopilot.reconstruct import _merge_semantic_secondary_ops

FIXTURE_PART = Path("runs/wear-plate/10_part_definition.json")


def _rect_part(width: float, height: float) -> PartDefinition:
    return PartDefinition(
        part_number="RECT",
        outer_contour=[
            {
                "id": "l0",
                "type": "line",
                "points": [
                    {"x": 0.0, "y": 0.0},
                    {"x": width, "y": 0.0},
                ],
            },
            {
                "id": "l1",
                "type": "line",
                "points": [
                    {"x": width, "y": 0.0},
                    {"x": width, "y": height},
                ],
            },
            {
                "id": "l2",
                "type": "line",
                "points": [
                    {"x": width, "y": height},
                    {"x": 0.0, "y": height},
                ],
            },
            {
                "id": "l3",
                "type": "line",
                "points": [
                    {"x": 0.0, "y": height},
                    {"x": 0.0, "y": 0.0},
                ],
            },
        ],
    )


def test_merge_semantic_secondary_ops_drops_unmatched_llm_op():
    geometric = [
        {"type": "countersink", "hole_id": "hole_00", "outer_diameter_mm": 34.0},
        {"type": "countersink", "hole_id": "hole_01", "outer_diameter_mm": 34.0},
    ]
    semantic = LLMSemanticResult(
        provider="groq",
        model="test",
        title_block=TitleBlockExtraction(),
        secondary_operations=[
            {
                "type": "countersink",
                "candidate_id": "contour_3",
                "notes": "Section A-A shows countersink profile.",
            }
        ],
    )
    merged = _merge_semantic_secondary_ops(geometric, semantic)
    assert len(merged) == 2
    assert all(op.get("notes") == "Section A-A shows countersink profile." for op in merged)
    assert all("candidate_id" not in op for op in merged)


def test_band_slicing_assigns_crossing_part_to_next_sheet():
    margin = 8.0
    sheet_width = 500.0
    footprints = [
        _PlacedFootprint(0, 0, 0, 0, 0, 200, 100),
        _PlacedFootprint(450, 0, 0, 450, 0, 650, 100),
    ]
    map_strip_to_sheets(footprints, sheet_width, margin)
    assert footprints[0].sheet_index == 0
    assert footprints[1].sheet_index == 1
    assert footprints[0].x_mm == pytest.approx(margin)
    assert footprints[1].x_mm == pytest.approx(margin)


def test_verify_nesting_flags_out_of_bounds_and_overlap():
    part = _rect_part(100.0, 50.0)
    nesting = NestingResult(
        strategy="test",
        sheet_width_mm=500.0,
        sheet_height_mm=300.0,
        quantity_requested=2,
        quantity_placed=2,
        sheet_count=1,
        utilization_pct=10.0,
        min_separation_mm=8.0,
        placements=[
            NestingPlacement(part_id="RECT", x_mm=-5.0, y_mm=10.0, rotation_deg=0.0, sheet_index=0),
            NestingPlacement(part_id="RECT", x_mm=20.0, y_mm=10.0, rotation_deg=0.0, sheet_index=0),
        ],
    )
    warnings = verify_nesting(part, nesting)
    assert any("outside the sheet" in w for w in warnings)
    assert any("overlap" in w or "closer than" in w for w in warnings)


def test_rectangular_part_nesting_stays_in_bounds():
    part = _rect_part(400.0, 200.0)
    engine = get_nesting_engine("spyrrow")
    nesting = engine.nest(part, 6, 2500.0, 1250.0, 3.0, 5.0, time_limit_s=5)
    item = normalized_part_polygon(part)
    assert item is not None
    for pl in nesting.placements:
        geom = placement_geometry(item, pl.x_mm, pl.y_mm, pl.rotation_deg)
        b = geom.bounds
        assert b[0] >= -0.05
        assert b[1] >= -0.05
        assert b[2] <= nesting.sheet_width_mm + 0.05
        assert b[3] <= nesting.sheet_height_mm + 0.05
    assert not any("outside the sheet" in w for w in nesting.warnings)


def test_rotation_is_stored_in_degrees_not_converted():
    """Spyrrow returns degrees; a small angle must not be multiplied by 57.3."""
    from unittest.mock import MagicMock, patch

    class FakePlaced:
        rotation = 3.0
        translation = (10.0, 20.0)

    class FakeSolution:
        width = 400.0
        density = 0.5
        placed_items = [FakePlaced()]

    class FakeInstance:
        def __init__(self, *args, **kwargs):
            pass

        def solve(self, config):
            return FakeSolution()

    fake_spyrrow = MagicMock()
    fake_spyrrow.StripPackingInstance = FakeInstance
    fake_spyrrow.Item = MagicMock
    fake_spyrrow.StripPackingConfig = MagicMock

    engine = SpyrrowNestingEngine()
    part = _rect_part(100.0, 50.0)
    with patch.dict("sys.modules", {"spyrrow": fake_spyrrow}):
        result = engine.nest(part, 1, 500.0, 300.0, 3.0, 5.0, time_limit_s=1)
    assert result.placements[0].rotation_deg == pytest.approx(3.0, abs=0.01)
    assert result.placements[0].rotation_deg != pytest.approx(171.9, abs=1.0)


@pytest.mark.skipif(not FIXTURE_PART.exists(), reason="wear-plate part artifact missing")
def test_wear_plate_nesting_utilization_at_qty_24():
    part = PartDefinition(**json.loads(FIXTURE_PART.read_text(encoding="utf-8")))
    engine = get_nesting_engine("spyrrow")
    nesting = engine.nest(part, 24, 2500.0, 1250.0, 3.0, 5.0, time_limit_s=10)
    assert nesting.sheet_count == 1
    assert nesting.utilization_pct > 60.0
    assert nesting.strip_density is not None
    assert nesting.strip_density > 0.75
    assert not any("outside the sheet" in w for w in nesting.warnings)


@pytest.mark.skipif(not FIXTURE_PART.exists(), reason="wear-plate part artifact missing")
def test_wear_plate_nesting_qty_4_uses_one_sheet():
    part = PartDefinition(**json.loads(FIXTURE_PART.read_text(encoding="utf-8")))
    engine = get_nesting_engine("spyrrow")
    nesting = engine.nest(part, 4, 2500.0, 1250.0, 3.0, 5.0, time_limit_s=8)
    assert nesting.sheet_count == 1
    assert nesting.quantity_placed == 4
    assert not any("outside the sheet" in w for w in nesting.warnings)


def test_grid_capacity_uses_the_better_orientation():
    # 583.2 x 185.0 on 2500 x 1250 with 8 mm separation fits 24 either way.
    assert grid_capacity_per_sheet(583.2, 185.0, 2500.0, 1250.0, 8.0) == 24
    # A part that only fits rotated must still report capacity.
    assert grid_capacity_per_sheet(1200.0, 100.0, 300.0, 2000.0, 5.0) > 0
    # A part larger than the sheet in both orientations fits nowhere.
    assert grid_capacity_per_sheet(3000.0, 3000.0, 2500.0, 1250.0, 5.0) == 0


def test_grid_fallback_matches_its_own_capacity_baseline():
    part = _rect_part(583.2, 185.0)
    engine = SpyrrowNestingEngine()
    result = engine._grid_fallback(
        part, 24, 2500.0, 1250.0, 8.0, 583.2, 185.0, 583.2 * 185.0
    )
    baseline = grid_capacity_per_sheet(583.2, 185.0, 2500.0, 1250.0, 8.0)
    assert result.sheet_count == 1
    assert result.max_parts_on_a_sheet == baseline
    assert not any("outside the sheet" in w for w in result.warnings)


def test_optimizer_underperforming_the_grid_falls_back():
    """A packer that wastes a sheet must be rejected in favour of the grid."""
    from unittest.mock import MagicMock, patch

    class FakePlaced:
        def __init__(self, translation):
            self.rotation = 0.0
            self.translation = translation

    class FakeSolution:
        width = 4000.0
        density = 0.1
        # Two items spread far apart so band slicing puts them on separate sheets,
        # even though a grid fits many per sheet.
        placed_items = [FakePlaced((0.0, 0.0)), FakePlaced((3000.0, 0.0))]

    class FakeInstance:
        def __init__(self, *args, **kwargs):
            pass

        def solve(self, config):
            return FakeSolution()

    fake_spyrrow = MagicMock()
    fake_spyrrow.StripPackingInstance = FakeInstance
    fake_spyrrow.Item = MagicMock
    fake_spyrrow.StripPackingConfig = MagicMock

    part = _rect_part(200.0, 100.0)
    engine = SpyrrowNestingEngine()
    with patch.dict("sys.modules", {"spyrrow": fake_spyrrow}):
        result = engine.nest(part, 2, 2500.0, 1250.0, 3.0, 5.0, time_limit_s=1)

    assert result.strategy == "grid_fallback"
    assert result.sheet_count == 1
    assert any("naive grid fits" in w for w in result.warnings)


def test_stale_sheet_renders_are_removed(tmp_path):
    from sheetcopilot.overlay import render_nest_preview

    part = _rect_part(200.0, 100.0)
    stale = tmp_path / "nest_sheet7.png"
    stale.write_bytes(b"not a real png")

    nesting = NestingResult(
        strategy="test",
        sheet_width_mm=1000.0,
        sheet_height_mm=800.0,
        quantity_requested=1,
        quantity_placed=1,
        sheet_count=1,
        utilization_pct=2.5,
        min_separation_mm=8.0,
        placements=[
            NestingPlacement(part_id="RECT", x_mm=10.0, y_mm=10.0, rotation_deg=0.0, sheet_index=0)
        ],
    )
    render_nest_preview(part, nesting, tmp_path / "nest_preview.png")

    assert not stale.exists()
    assert (tmp_path / "nest_sheet1.png").exists()
