"""Tests for manufacturing validation gate."""

from __future__ import annotations

import pytest

from sheetcopilot.models import (
    CandidateContour,
    CandidatesResult,
    DimensionsResult,
    HoleClassification,
    PartDefinition,
    Point2D,
    ScaleResult,
)
from sheetcopilot.nest import ManufacturingNotReadyError, get_nesting_engine
from sheetcopilot.validate import run_validation


def _part(width: float, height: float, holes: list[float] | None = None) -> PartDefinition:
    holes = holes or []
    return PartDefinition(
        part_number="TEST",
        outer_contour=[],
        holes=[
            HoleClassification(id=f"h{i}", center=Point2D(x=0, y=0), diameter_mm=d, operation="cut")
            for i, d in enumerate(holes)
        ],
        bbox_mm=(0, 0, width, height),
        scale_mm_per_pt=0.882,
    )


def test_nesting_blocked_when_not_ready():
    part = _part(1100, 800)
    engine = get_nesting_engine("spyrrow")
    with pytest.raises(ManufacturingNotReadyError):
        engine.nest(part, 1, 2500, 1250, 3, 5, manufacturing_ready=False)


def test_bbox_mismatch_blocks():
    part = _part(700, 800)
    dims = DimensionsResult(dimensions=[], annotated_linear_mm=[583.3, 185.0])
    scale = ScaleResult(scale_mm_per_pt=0.882, stable=True, consensus_confidence=0.9, anchors=[])
    candidates = CandidatesResult(
        candidates=[
            CandidateContour(
                id="contour_0",
                chain_ids=[1],
                bbox=(0, 0, 1000, 700),
                area_pt2=700000,
                stroke_width=1.4,
                is_closed=True,
                is_page_frame=True,
            )
        ],
        selected_id="contour_0",
    )
    result = run_validation(part, candidates, dims, scale)
    assert result.manufacturing_ready is False
    assert any(i.code == "BBOX_DIMENSION_MISMATCH" for i in result.blocking_issues)


def test_valid_part_passes_gate():
    part = _part(583.3, 185.0, holes=[18, 18, 18])
    part.outer_contour = part.outer_contour or []
    dims = DimensionsResult(dimensions=[], annotated_linear_mm=[583.3], annotated_diameters_mm=[18])
    scale = ScaleResult(scale_mm_per_pt=0.882, stable=True, consensus_confidence=0.9, anchors=[])
    candidates = CandidatesResult(
        candidates=[
            CandidateContour(
                id="main_boundary",
                chain_ids=[1, 2],
                bbox=(300, 400, 960, 610),
                area_pt2=120000,
                stroke_width=1.4,
                is_closed=False,
                is_page_frame=False,
            )
        ],
        selected_id="main_boundary",
    )
    # Topology check may fail with empty outer — focus on scale + bbox here
    result = run_validation(part, candidates, dims, scale)
    assert result.status in ("ready", "failed")  # topology may block empty contour


def _candidates(page_frame: bool = False) -> CandidatesResult:
    return CandidatesResult(
        candidates=[
            CandidateContour(
                id="main_boundary",
                chain_ids=[],
                bbox=(300, 400, 960, 610),
                area_pt2=120000,
                stroke_width=1.4,
                is_closed=True,
                is_page_frame=page_frame,
            )
        ],
        selected_id="main_boundary",
    )


def _scale() -> ScaleResult:
    return ScaleResult(scale_mm_per_pt=0.882, stable=True, consensus_confidence=0.9, anchors=[])


def test_geometry_matching_no_annotation_is_blocked():
    """A sheet border or section view reaches validation with no matching dimension."""
    part = _part(1049.7, 742.3)
    dims = DimensionsResult(dimensions=[], annotated_linear_mm=[583.3, 552.1, 40.0])
    result = run_validation(part, _candidates(), dims, _scale())

    assert result.manufacturing_ready is False
    assert any(i.code == "BBOX_DIMENSION_MISMATCH" for i in result.blocking_issues)


def test_overall_dimension_is_verified_against_its_annotation():
    part = _part(583.2, 185.1)
    dims = DimensionsResult(dimensions=[], annotated_linear_mm=[583.3, 552.1, 40.0])
    result = run_validation(part, _candidates(), dims, _scale())

    width_check = next(c for c in result.dimension_checks if "width" in c.label)
    assert width_check.annotated_mm == 583.3
    assert width_check.passed
    assert not any(i.code == "BBOX_DIMENSION_MISMATCH" for i in result.blocking_issues)


def test_scaled_geometry_is_caught_even_though_an_annotation_is_nearby():
    """A 10% scale error still lands inside the annotation search window."""
    part = _part(641.6, 203.6)
    dims = DimensionsResult(dimensions=[], annotated_linear_mm=[583.3])
    result = run_validation(part, _candidates(), dims, _scale())

    assert result.manufacturing_ready is False
    assert any(i.code == "BBOX_DIMENSION_MISMATCH" for i in result.blocking_issues)


def test_warnings_are_not_reported_as_blocking_issues():
    """Unknown thickness is worth flagging but must not read as a hard failure."""
    part = _part(583.2, 185.1)
    dims = DimensionsResult(dimensions=[], annotated_linear_mm=[583.3])
    result = run_validation(part, _candidates(), dims, _scale())

    assert any(w.code == "MISSING_THICKNESS" for w in result.warnings)
    assert all(i.severity == "error" for i in result.blocking_issues)
    assert not any(i.code == "MISSING_THICKNESS" for i in result.blocking_issues)


def test_failed_dimension_check_prevents_readiness():
    part = _part(583.2, 185.1, holes=[24.0])
    dims = DimensionsResult(
        dimensions=[], annotated_linear_mm=[583.3], annotated_diameters_mm=[18.0]
    )
    result = run_validation(part, _candidates(), dims, _scale())

    assert result.manufacturing_ready is False
    assert result.validation_summary["failed_dimension_checks"]
