"""Unit tests for scale consensus."""

from __future__ import annotations

import pytest

from sheetcopilot.models import ScaleAnchor, ScaleResult
from sheetcopilot.scale import compute_scale_consensus, parse_scale_ratio


def test_parse_scale_ratio():
    assert parse_scale_ratio("1:2.5") == pytest.approx(2.5)
    assert parse_scale_ratio("1:5") == pytest.approx(5.0)


def test_scale_outlier_rejected():
    anchors = [
        ScaleAnchor(
            annotation_mm=583.3,
            measured_pt=661.0,
            scale_mm_per_pt=0.8823,
            error_percent=0,
            status="accepted",
            dim_type="linear_width",
        ),
        ScaleAnchor(
            annotation_mm=583.3,
            measured_pt=630.0,
            scale_mm_per_pt=0.9259,
            error_percent=0,
            status="accepted",
            dim_type="linear_width",
        ),
        ScaleAnchor(
            annotation_mm=0,
            measured_pt=0,
            scale_mm_per_pt=0.8819,
            error_percent=0,
            status="accepted",
            dim_type="scale_prior",
        ),
    ]
    result: ScaleResult = compute_scale_consensus(anchors)
    assert result.scale_mm_per_pt == pytest.approx(0.8823, rel=0.01)
    assert result.stable is True


def test_scale_prior_accepted_when_no_conflicting_anchors():
    anchors = [
        ScaleAnchor(
            annotation_mm=0,
            measured_pt=0,
            scale_mm_per_pt=0.881944,
            error_percent=0,
            status="accepted",
            dim_type="scale_prior",
        ),
    ]
    result = compute_scale_consensus(anchors)
    assert result.stable is True
    assert result.scale_mm_per_pt == pytest.approx(0.881944, rel=1e-4)
