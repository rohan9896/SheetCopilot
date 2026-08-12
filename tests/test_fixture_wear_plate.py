"""Regression tests against fixtures/wear_plate expected values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from sheetcopilot.dxf import export_part_dxf, verify_dxf_roundtrip
from sheetcopilot.models import PartDefinition, ValidationResult
from sheetcopilot.nest import ManufacturingNotReadyError, get_nesting_engine, part_to_polygon
from sheetcopilot.overlay import render_cut_preview
from sheetcopilot.pipeline import run_pipeline_stages
from sheetcopilot.reconstruct import _arc_extremes

FIXTURE_PDF = Path("fixtures/wear_plate/input.pdf")
EXPECTED = json.loads((Path("fixtures/wear_plate/expected.json")).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def wear_plate_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    run_dir = tmp_path_factory.mktemp("wear-plate")
    return run_pipeline_stages(FIXTURE_PDF, run_dir, provider="heuristic", render=False)


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_manufacturing_ready(wear_plate_run: dict) -> None:
    validation: ValidationResult = wear_plate_run["validation"]
    assert validation.manufacturing_ready is True
    assert validation.status == "ready"


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_bbox(wear_plate_run: dict) -> None:
    part: PartDefinition = wear_plate_run["part"]
    exp = EXPECTED["outer"]
    tol = exp["tolerance_mm"]
    bbox = part.bbox_mm
    assert bbox is not None
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    assert width == pytest.approx(exp["width_mm"], abs=tol)
    assert height == pytest.approx(exp["height_mm"], abs=tol)


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_outer_contour_is_clean_geometry(wear_plate_run: dict) -> None:
    """The plate is a handful of lines and two arcs, not a dense noisy polyline."""
    part: PartDefinition = wear_plate_run["part"]
    exp = EXPECTED["outer"]
    assert len(part.outer_contour) <= exp["max_primitives"]

    radii = sorted(p.radius_mm for p in part.outer_contour if p.type == "arc")
    assert len(radii) == len(exp["arc_radii_mm"])
    for measured, expected in zip(radii, sorted(exp["arc_radii_mm"])):
        assert measured == pytest.approx(expected, rel=exp["arc_radius_tolerance_pct"] / 100)


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_cut_area_matches_the_plate(wear_plate_run: dict) -> None:
    """A contour polluted by annotation or section geometry would not hold area."""
    part: PartDefinition = wear_plate_run["part"]
    poly = part_to_polygon(part)
    assert poly.area == pytest.approx(EXPECTED["outer"]["area_mm2"], rel=0.02)


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_cut_geometry_stays_inside_the_main_view(wear_plate_run: dict) -> None:
    """No title block, section view or annotation geometry may reach the CUT layer."""
    part: PartDefinition = wear_plate_run["part"]
    regions = wear_plate_run["regions"]
    main = next(r for r in regions.regions if r.id == regions.main_view_id)
    k = part.scale_mm_per_pt
    x0, y0, x1, y1 = (v * k for v in main.bbox)

    for prim in part.outer_contour + part.internal_features:
        # A large-radius arc has its centre far off the sheet, so compare the
        # geometry it actually sweeps rather than its construction centre.
        if prim.type == "arc":
            points = _arc_extremes(prim)
        elif prim.type == "circle" and prim.center:
            r = prim.radius_mm or 0.0
            points = [
                (prim.center.x - r, prim.center.y - r),
                (prim.center.x + r, prim.center.y + r),
            ]
        else:
            points = [(p.x, p.y) for p in prim.points]

        for px, py in points:
            assert x0 <= px <= x1, f"{prim.id} escapes main view in x"
            assert y0 <= py <= y1, f"{prim.id} escapes main view in y"


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_cut_preview_is_written(wear_plate_run: dict, tmp_path: Path) -> None:
    part: PartDefinition = wear_plate_run["part"]
    out = tmp_path / "cut_preview.png"
    render_cut_preview(part, out)
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_scale(wear_plate_run: dict) -> None:
    part: PartDefinition = wear_plate_run["part"]
    exp = EXPECTED["scale"]
    err_pct = abs(part.scale_mm_per_pt - exp["mm_per_pt"]) / exp["mm_per_pt"] * 100
    assert err_pct <= exp["tolerance_pct"]


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_holes(wear_plate_run: dict) -> None:
    part: PartDefinition = wear_plate_run["part"]
    exp = EXPECTED["holes"][0]
    cut = [h for h in part.holes if h.operation == "cut"]
    assert len(cut) == exp["count"]
    for h in cut:
        assert h.diameter_mm == pytest.approx(exp["diameter_mm"], abs=exp["tolerance_mm"])


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_secondary_ops(wear_plate_run: dict) -> None:
    part: PartDefinition = wear_plate_run["part"]
    exp = EXPECTED["secondary_operations"][0]
    ops = [o for o in part.secondary_operations if o.get("type") == exp["type"]]
    assert len(ops) >= exp["count"]
    for op in ops[: exp["count"]]:
        dia = op.get("outer_diameter_mm") or op.get("diameter_mm")
        assert dia == pytest.approx(exp["diameter_mm"], abs=exp["tolerance_mm"])


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_contour_topology(wear_plate_run: dict) -> None:
    part: PartDefinition = wear_plate_run["part"]
    poly = part_to_polygon(part)
    assert poly is not None
    assert poly.is_valid


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_no_page_frame_bbox(wear_plate_run: dict) -> None:
    part: PartDefinition = wear_plate_run["part"]
    max_w = EXPECTED["prohibited"]["max_bbox_width_mm"]
    bbox = part.bbox_mm
    assert bbox is not None
    assert bbox[2] - bbox[0] < max_w


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_wear_plate_dxf_roundtrip(wear_plate_run: dict, tmp_path: Path) -> None:
    part: PartDefinition = wear_plate_run["part"]
    dxf_path = tmp_path / "wear_plate.dxf"
    export_part_dxf(part, dxf_path)
    passed, _, _ = verify_dxf_roundtrip(dxf_path, part)
    assert passed


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="wear plate fixture missing")
def test_nesting_requires_manufacturing_ready(wear_plate_run: dict) -> None:
    part: PartDefinition = wear_plate_run["part"]
    engine = get_nesting_engine("spyrrow")
    with pytest.raises(ManufacturingNotReadyError):
        engine.nest(part, 1, 2500, 1250, 3, 5, manufacturing_ready=False)
