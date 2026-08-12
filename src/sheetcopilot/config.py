"""Configurable tolerances for geometry validation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tolerances:
    """All tolerance values — no magic numbers at call sites."""

    # Chaining / cleanup (PDF points)
    chain_tol_pt: float = 0.75
    snap_tol_pt: float = 0.35
    min_segment_pt: float = 0.05
    max_chain_gap_pt: float = 2.0

    # Loop extraction
    loop_join_tol_pt: float = 0.5
    corner_angle_deg: float = 12.0
    arc_corner_angle_deg: float = 25.0
    straight_run_total_turn_deg: float = 1.5
    arc_fit_max_deviation_pt: float = 0.6
    circle_fit_max_deviation_ratio: float = 0.06
    min_profile_area_ratio: float = 0.001
    stroke_width_match_tol_pt: float = 0.15
    min_circle_coverage_deg: float = 180.0

    # Region detection
    page_frame_area_ratio: float = 0.85
    page_frame_edge_margin_pt: float = 5.0
    page_frame_margin_frac: float = 0.02
    title_block_x_frac: float = 0.55
    title_block_y_frac: float = 0.65
    main_view_pad_pt: float = 6.0

    # Stroke width thresholds (PDF points)
    thick_stroke_pt: float = 1.0
    thin_stroke_pt: float = 0.8
    arrowhead_max_size_pt: float = 15.0

    # Scale consensus
    scale_outlier_pct: float = 2.0
    scale_min_anchors: int = 2
    scale_linear_tolerance_pct: float = 3.0
    scale_radius_tolerance_pct: float = 1.5

    # Dimension association
    dimension_text_proximity_pt: float = 120.0

    # Hole detection
    overall_dim_search_frac: float = 0.25
    hole_diameter_tolerance_mm: float = 0.5
    hole_diameter_match_tol: float = 0.08
    hole_concentric_tol_pt: float = 3.0
    hole_min_diameter_mm: float = 2.0
    hole_max_diameter_mm: float = 200.0

    # Validation
    bbox_tolerance_mm: float = 2.0
    dimension_tolerance_mm: float = 0.5
    arc_fit_max_deviation_ratio: float = 0.06
    arc_fit_min_points: int = 8

    # DXF round-trip
    dxf_area_tolerance_pct: float = 2.0
    dxf_bbox_tolerance_mm: float = 1.0


TOLERANCES = Tolerances()
