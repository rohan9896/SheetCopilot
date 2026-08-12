"""Concentric hole grouping and CUT vs SECONDARY_OP resolution."""

from __future__ import annotations

import math

from sheetcopilot.config import TOLERANCES
from sheetcopilot.models import (
    ChainAnalysis,
    ChainsResult,
    FeaturesResult,
    GeometryPrimitive,
    HoleClassification,
    HoleFeature,
    Point2D,
    ScaleResult,
    Segment,
)
from sheetcopilot.loops import CircleFeature, PartProfile
from sheetcopilot.regions import get_region, RegionsResult


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def find_circle_chains_in_main_view(
    chains: ChainsResult,
    regions: RegionsResult,
    scale_mm_per_pt: float,
    segments: list[Segment] | None = None,
) -> list[tuple[ChainAnalysis, float, float, float]]:
    """Return (chain, cx, cy, radius_pt) for circle-like geometry in main view."""
    main = get_region(regions, "main_view") if regions.main_view_id else None
    results: list[tuple[ChainAnalysis, float, float, float]] = []

    for ca in chains.chains:
        if ca.fitted_circle:
            ch = ca.chain
            fc = ca.fitted_circle
            cx, cy = fc.center.x, fc.center.y
            if main and not (main.bbox[0] <= cx <= main.bbox[2] and main.bbox[1] <= cy <= main.bbox[3]):
                continue
            if fc.radius_pt < 3 or fc.radius_pt > 50:
                continue
            results.append((ca, cx, cy, fc.radius_pt))

    # Also detect polyline circles from segment grouping
    if segments and main:
        poly_circles = _detect_polyline_circles(segments, main.bbox)
        for cx, cy, r_pt in poly_circles:
            # Create synthetic entry (no ChainAnalysis)
            from sheetcopilot.models import Chain, ChainPoint, FittedCircle, Point2D

            synthetic = ChainAnalysis(
                chain=Chain(
                    id=-1,
                    points=[ChainPoint(x=cx, y=cy)],
                    stroke_width=TOLERANCES.thick_stroke_pt,
                    closed=True,
                    segment_ids=[],
                    region_id="main_view",
                ),
                fitted_circle=FittedCircle(
                    center=Point2D(x=cx, y=cy),
                    radius_pt=r_pt,
                    max_deviation_pt=0.2,
                    confidence=0.8,
                ),
                is_circular=True,
                bbox=(cx - r_pt, cy - r_pt, cx + r_pt, cy + r_pt),
            )
            results.append((synthetic, cx, cy, r_pt))

    return results


def _detect_polyline_circles(
    segments: list[Segment],
    main_bbox: tuple[float, float, float, float],
    min_r: float = 5.0,
    max_r: float = 25.0,
) -> list[tuple[float, float, float]]:
    """Detect circles formed by short polyline segments in main view."""
    from sheetcopilot.chains import fit_circle

    eligible = [
        s
        for s in segments
        if s.stroke_width >= TOLERANCES.thick_stroke_pt
        and s.category.value in ("manufacturing_candidate", "unknown")
    ]
    # Filter to main view
    in_view = []
    for s in eligible:
        cx = (s.start.x + s.end.x) / 2
        cy = (s.start.y + s.end.y) / 2
        if main_bbox[0] <= cx <= main_bbox[2] and main_bbox[1] <= cy <= main_bbox[3]:
            length = math.hypot(s.end.x - s.start.x, s.end.y - s.start.y)
            if length < 8.0:
                in_view.append(s)

    if len(in_view) < 6:
        return []

    # Group by connectivity
    parent = list(range(len(in_view)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    def near(x1, y1, x2, y2) -> bool:
        return math.hypot(x1 - x2, y1 - y2) <= TOLERANCES.snap_tol_pt

    for i, si in enumerate(in_view):
        for j in range(i + 1, len(in_view)):
            sj = in_view[j]
            for (x1, y1), (x2, y2) in [
                ((si.start.x, si.start.y), (sj.start.x, sj.start.y)),
                ((si.start.x, si.start.y), (sj.end.x, sj.end.y)),
                ((si.end.x, si.end.y), (sj.start.x, sj.start.y)),
                ((si.end.x, si.end.y), (sj.end.x, sj.end.y)),
            ]:
                if near(x1, y1, x2, y2):
                    union(i, j)
                    break

    groups: dict[int, list] = {}
    for i, s in enumerate(in_view):
        groups.setdefault(find(i), []).append(s)

    circles: list[tuple[float, float, float]] = []
    for group in groups.values():
        if len(group) < 6:
            continue
        pts = [(s.start.x, s.start.y) for s in group] + [(s.end.x, s.end.y) for s in group]
        fit = fit_circle(pts)
        if not fit:
            continue
        cx, cy, r, dev = fit
        if not (min_r <= r <= max_r):
            continue
        if dev / r > 0.08:
            continue
        circles.append((cx, cy, r))

    return circles


def group_concentric_circles(
    circles: list[tuple[ChainAnalysis, float, float, float]],
    tol_pt: float | None = None,
) -> list[list[tuple[ChainAnalysis, float, float, float]]]:
    """Group near-concentric circles into logical hole features."""
    tol = tol_pt or TOLERANCES.hole_concentric_tol_pt
    groups: list[list[tuple[ChainAnalysis, float, float, float]]] = []
    used = [False] * len(circles)

    for i, (ca_i, cx_i, cy_i, r_i) in enumerate(circles):
        if used[i]:
            continue
        group = [(ca_i, cx_i, cy_i, r_i)]
        used[i] = True
        for j, (ca_j, cx_j, cy_j, r_j) in enumerate(circles):
            if used[j]:
                continue
            if _dist((cx_i, cy_i), (cx_j, cy_j)) <= tol:
                group.append((ca_j, cx_j, cy_j, r_j))
                used[j] = True
        groups.append(group)
    return groups


def resolve_hole_group(
    group: list[tuple[ChainAnalysis, float, float, float]],
    scale_mm_per_pt: float,
    annotated_diameters: list[float],
    hole_idx: int,
) -> HoleFeature:
    """Pick CUT diameter (matches smallest annotated through-hole) and SECONDARY for larger rings."""
    sorted_group = sorted(group, key=lambda g: g[3])
    cx = sum(g[1] for g in group) / len(group)
    cy = sum(g[2] for g in group) / len(group)

    candidate_ids = [f"chain_{g[0].chain.id}" for g in group]
    secondary_ops: list[dict] = []
    cut_prim: GeometryPrimitive | None = None
    through_dia: float | None = None

    through_anns = sorted(d for d in annotated_diameters if 10 <= d <= 30)
    secondary_anns = sorted(d for d in annotated_diameters if 30 < d <= 50)

    cut_entry: tuple[ChainAnalysis, float, float, float] | None = None
    if through_anns:
        target = through_anns[0]
        best_err = float("inf")
        for entry in sorted_group:
            dia_mm = 2 * entry[3] * scale_mm_per_pt
            err = abs(dia_mm - target) / target
            if err < best_err:
                best_err = err
                cut_entry = entry
        if cut_entry and best_err > 0.15:
            cut_entry = sorted_group[0] if sorted_group[0][3] < sorted_group[-1][3] else None
    elif sorted_group:
        cut_entry = sorted_group[0]

    if cut_entry:
        ca, _, _, r_pt = cut_entry
        dia_mm = 2 * r_pt * scale_mm_per_pt
        # If only large ring(s) present, treat as secondary when through-hole annotation exists
        if through_anns and dia_mm > through_anns[0] * 1.4:
            cut_entry = None
        else:
            through_dia = dia_mm
            cut_prim = GeometryPrimitive(
                id=f"hole_{hole_idx}_cut",
                type="circle",
                center=Point2D(x=cx * scale_mm_per_pt, y=cy * scale_mm_per_pt),
                radius_mm=dia_mm / 2,
                source_pdf_object_ids=ca.chain.segment_ids,
                geometry_confidence=1.0
                - (ca.fitted_circle.max_deviation_pt / r_pt if ca.fitted_circle and r_pt else 0.5),
            )

    # Infer through-hole at center when only countersink rings were detected
    if cut_entry is None and through_anns and sorted_group:
        through_dia = through_anns[0]
        cut_prim = GeometryPrimitive(
            id=f"hole_{hole_idx}_cut_inferred",
            type="circle",
            center=Point2D(x=cx * scale_mm_per_pt, y=cy * scale_mm_per_pt),
            radius_mm=through_dia / 2,
            source_pdf_object_ids=sorted_group[0][0].chain.segment_ids,
            geometry_confidence=0.6,
        )

    for ca, _, _, r_pt in sorted_group:
        if cut_entry and ca is cut_entry[0]:
            continue
        dia_mm = 2 * r_pt * scale_mm_per_pt
        is_secondary = dia_mm > (through_dia or 0) * 1.2
        if not is_secondary and secondary_anns:
            is_secondary = any(abs(dia_mm - ann) / ann <= 0.08 for ann in secondary_anns)
        if is_secondary:
            secondary_ops.append(
                {
                    "type": "countersink",
                    "hole_id": f"hole_{hole_idx:02d}",
                    "outer_diameter_mm": dia_mm,
                    "candidate_ids": [f"chain_{ca.chain.id}"],
                }
            )

    return HoleFeature(
        id=f"hole_{hole_idx:02d}",
        center=Point2D(x=cx * scale_mm_per_pt, y=cy * scale_mm_per_pt),
        through_diameter_mm=through_dia,
        candidate_geometry_ids=candidate_ids,
        cut_circle=cut_prim,
        secondary_ops=secondary_ops,
    )


def group_concentric_features(
    circles: list[CircleFeature],
    tol_pt: float | None = None,
) -> list[list[CircleFeature]]:
    """Group circles sharing a centre into one logical hole."""
    tol = tol_pt or TOLERANCES.hole_concentric_tol_pt
    groups: list[list[CircleFeature]] = []
    for circle in sorted(circles, key=lambda c: c.radius_pt):
        for group in groups:
            if _dist(circle.center, group[0].center) <= tol:
                group.append(circle)
                break
        else:
            groups.append([circle])
    return groups


def resolve_profile_hole_group(
    group: list[CircleFeature],
    scale_mm_per_pt: float,
    annotated_diameters: list[float],
    hole_idx: int,
) -> HoleFeature:
    """
    Resolve one concentric group into a CUT circle plus secondary operations.

    The smallest ring is the through hole that gets cut; larger concentric rings
    are countersinks or spot faces and are recorded as secondary operations so
    they never widen the cut geometry.
    """
    ordered = sorted(group, key=lambda c: c.radius_pt)
    cx = sum(c.center[0] for c in ordered) / len(ordered)
    cy = sum(c.center[1] for c in ordered) / len(ordered)

    cut = ordered[0]
    cut_dia = cut.diameter_pt * scale_mm_per_pt

    matched = None
    for ann in sorted(annotated_diameters):
        if ann > 0 and abs(cut_dia - ann) / ann <= TOLERANCES.hole_diameter_match_tol:
            matched = ann
            break

    cut_prim = GeometryPrimitive(
        id=f"hole_{hole_idx:02d}_cut",
        type="circle",
        center=Point2D(x=cx * scale_mm_per_pt, y=cy * scale_mm_per_pt),
        radius_mm=cut_dia / 2,
        source_pdf_object_ids=cut.segment_ids,
        geometry_confidence=max(0.5, 1.0 - cut.max_deviation_pt / max(cut.radius_pt, 1e-6)),
        semantic_confidence=0.9 if matched else 0.5,
    )

    secondary_ops = [
        {
            "type": "countersink",
            "hole_id": f"hole_{hole_idx:02d}",
            "outer_diameter_mm": ring.diameter_pt * scale_mm_per_pt,
            "candidate_ids": ring.segment_ids[:8],
        }
        for ring in ordered[1:]
    ]

    return HoleFeature(
        id=f"hole_{hole_idx:02d}",
        center=Point2D(x=cx * scale_mm_per_pt, y=cy * scale_mm_per_pt),
        through_diameter_mm=cut_dia,
        candidate_geometry_ids=[sid for c in ordered for sid in c.segment_ids[:4]],
        cut_circle=cut_prim,
        secondary_ops=secondary_ops,
    )


def run_features_from_profile(
    profile: PartProfile,
    scale: ScaleResult,
    annotated_diameters: list[float],
) -> FeaturesResult:
    """Group the profile's circles into holes with CUT and secondary operations."""
    hole_features: list[HoleFeature] = []
    all_secondary: list[dict] = []

    groups = group_concentric_features(profile.circles)
    groups.sort(key=lambda g: (g[0].center[0], g[0].center[1]))

    for idx, group in enumerate(groups):
        hf = resolve_profile_hole_group(group, scale.scale_mm_per_pt, annotated_diameters, idx)
        hole_features.append(hf)
        all_secondary.extend(hf.secondary_ops)

    return FeaturesResult(
        hole_features=hole_features,
        secondary_operations=all_secondary,
    )


def run_features(
    chains: ChainsResult,
    regions: RegionsResult,
    scale: ScaleResult,
    annotated_diameters: list[float],
    segments: list | None = None,
    profile: PartProfile | None = None,
) -> FeaturesResult:
    """Group holes and resolve CUT vs secondary."""
    if profile is not None:
        return run_features_from_profile(profile, scale, annotated_diameters)

    circles = find_circle_chains_in_main_view(
        chains, regions, scale.scale_mm_per_pt, segments=segments
    )
    groups = group_concentric_circles(circles)

    hole_features: list[HoleFeature] = []
    all_secondary: list[dict] = []

    for idx, group in enumerate(groups):
        hf = resolve_hole_group(group, scale.scale_mm_per_pt, annotated_diameters, idx)
        hole_features.append(hf)
        all_secondary.extend(hf.secondary_ops)

    return FeaturesResult(
        hole_features=hole_features,
        secondary_operations=all_secondary,
    )
