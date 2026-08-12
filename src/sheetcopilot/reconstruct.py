"""Assemble canonical Part Definition from selected candidate + features."""

from __future__ import annotations

import math

from sheetcopilot.loops import PartProfile, loop_to_primitives
from sheetcopilot.models import (
    CandidateContour,
    CandidatesResult,
    ChainAnalysis,
    ChainsResult,
    Confidence,
    ConfidenceBundle,
    FeaturesResult,
    GeometryPrimitive,
    HoleClassification,
    LLMSemanticResult,
    PartDefinition,
    Point2D,
    ScaleResult,
    TitleBlockExtraction,
)


def _chain_to_primitives(
    ca: ChainAnalysis,
    scale_mm_per_pt: float,
    prefix: str,
    region_id: str | None = None,
) -> list[GeometryPrimitive]:
    """Convert one chain to LINE/ARC/CIRCLE primitives preserving geometry type."""
    primitives: list[GeometryPrimitive] = []
    ch = ca.chain
    pts = ch.points
    prov_ids = ch.segment_ids

    if ca.fitted_circle and ch.closed:
        c = ca.fitted_circle
        primitives.append(
            GeometryPrimitive(
                id=f"{prefix}_circle",
                type="circle",
                center=Point2D(x=c.center.x * scale_mm_per_pt, y=c.center.y * scale_mm_per_pt),
                radius_mm=c.radius_pt * scale_mm_per_pt,
                source_pdf_object_ids=prov_ids,
                region_id=region_id,
                geometry_confidence=c.confidence,
            )
        )
        return primitives

    if ca.fitted_arc and not ch.closed and len(pts) >= 3:
        a = ca.fitted_arc
        start = pts[0]
        end = pts[-1]
        start_angle = math.degrees(math.atan2(start.y - a.center.y, start.x - a.center.x))
        end_angle = math.degrees(math.atan2(end.y - a.center.y, end.x - a.center.x))
        primitives.append(
            GeometryPrimitive(
                id=f"{prefix}_arc",
                type="arc",
                center=Point2D(x=a.center.x * scale_mm_per_pt, y=a.center.y * scale_mm_per_pt),
                radius_mm=a.radius_pt * scale_mm_per_pt,
                start_angle_deg=start_angle,
                end_angle_deg=end_angle,
                source_pdf_object_ids=prov_ids,
                region_id=region_id,
                geometry_confidence=a.confidence,
            )
        )
        return primitives

    for i in range(len(pts) - 1):
        p0, p1 = pts[i], pts[i + 1]
        primitives.append(
            GeometryPrimitive(
                id=f"{prefix}_line_{i}",
                type="line",
                points=[
                    Point2D(x=p0.x * scale_mm_per_pt, y=p0.y * scale_mm_per_pt),
                    Point2D(x=p1.x * scale_mm_per_pt, y=p1.y * scale_mm_per_pt),
                ],
                source_pdf_object_ids=prov_ids,
                region_id=region_id,
            )
        )

    if ch.closed and len(pts) >= 2:
        p0, p1 = pts[-1], pts[0]
        primitives.append(
            GeometryPrimitive(
                id=f"{prefix}_close",
                type="line",
                points=[
                    Point2D(x=p0.x * scale_mm_per_pt, y=p0.y * scale_mm_per_pt),
                    Point2D(x=p1.x * scale_mm_per_pt, y=p1.y * scale_mm_per_pt),
                ],
                source_pdf_object_ids=prov_ids,
                region_id=region_id,
            )
        )

    return primitives


def _resolve_candidate_id(
    semantic: LLMSemanticResult,
    candidates: CandidatesResult,
) -> str | None:
    """Resolve outer contour candidate from LLM or deterministic selection."""
    if semantic.outer_contour_candidate_id:
        return semantic.outer_contour_candidate_id
    if semantic.primary_contour_id is not None:
        pid = str(semantic.primary_contour_id)
        for c in candidates.candidates:
            if c.id == pid or c.id == f"contour_{pid}":
                return c.id
    return candidates.selected_id


def _get_candidate(candidates: CandidatesResult, cid: str) -> CandidateContour | None:
    for c in candidates.candidates:
        if c.id == cid:
            return c
    return None


def _arc_extremes(prim: GeometryPrimitive) -> list[tuple[float, float]]:
    """Points bounding an arc: its endpoints plus any axis crossing it sweeps."""
    if not prim.center or not prim.radius_mm:
        return []
    cx, cy, r = prim.center.x, prim.center.y, prim.radius_mm
    start = prim.start_angle_deg or 0.0
    end = prim.end_angle_deg if prim.end_angle_deg is not None else 360.0
    sweep = (end - start) % 360.0
    if sweep == 0.0 and end != start:
        sweep = 360.0

    points = [
        (cx + r * math.cos(math.radians(start)), cy + r * math.sin(math.radians(start))),
        (cx + r * math.cos(math.radians(end)), cy + r * math.sin(math.radians(end))),
    ]
    for axis_deg, point in (
        (0.0, (cx + r, cy)),
        (90.0, (cx, cy + r)),
        (180.0, (cx - r, cy)),
        (270.0, (cx, cy - r)),
    ):
        if (axis_deg - start) % 360.0 <= sweep:
            points.append(point)
    return points


def _op_diameter_mm(op: dict) -> float | None:
    for key in ("outer_diameter_mm", "diameter_mm"):
        val = op.get(key)
        if val is not None:
            return float(val)
    return None


def _merge_semantic_secondary_ops(
    geometric_ops: list[dict],
    semantic: LLMSemanticResult,
    diameter_tol_mm: float = 2.0,
) -> list[dict]:
    """
    Merge LLM secondary-operation notes onto matching geometric ops.

    An LLM op matches when its type equals a geometric op's type and, when the
    LLM supplies a diameter, it is close to the geometric outer/diameter value.
    Unmatched LLM ops are dropped (e.g. section-view prose with no geometry).
    """
    merged = [dict(op) for op in geometric_ops]

    llm_ops: list[dict] = [dict(op) for op in semantic.secondary_operations]
    for hole in semantic.holes:
        if hole.operation == "secondary":
            llm_ops.append(
                {
                    "type": hole.secondary_type or "secondary",
                    "diameter_mm": hole.diameter_mm,
                    "notes": hole.notes,
                }
            )

    for llm_op in llm_ops:
        op_type = llm_op.get("type", "secondary")
        llm_diameter = _op_diameter_mm(llm_op)
        llm_notes = llm_op.get("notes")

        matches = [
            idx
            for idx, geom_op in enumerate(merged)
            if geom_op.get("type") == op_type
            and (
                llm_diameter is None
                or (
                    (d := _op_diameter_mm(geom_op)) is not None
                    and abs(d - llm_diameter) <= diameter_tol_mm
                )
            )
        ]
        if not matches:
            continue

        for idx in matches:
            if llm_notes and not merged[idx].get("notes"):
                merged[idx]["notes"] = llm_notes

    return merged


def _compute_bbox_mm(outer: list[GeometryPrimitive]) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for prim in outer:
        for p in prim.points:
            xs.append(p.x)
            ys.append(p.y)
        if prim.type == "circle" and prim.center:
            r = prim.radius_mm or 0
            xs.extend([prim.center.x - r, prim.center.x + r])
            ys.extend([prim.center.y - r, prim.center.y + r])
        elif prim.type == "arc":
            for x, y in _arc_extremes(prim):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def assemble_part_definition(
    chains: ChainsResult,
    semantic: LLMSemanticResult,
    scale: ScaleResult,
    candidates: CandidatesResult,
    features: FeaturesResult,
    main_view_bbox: tuple[float, float, float, float] | None = None,
    profile: PartProfile | None = None,
) -> PartDefinition:
    """Build Part Definition from exactly one selected outer contour candidate."""
    tb = semantic.title_block
    scale_k = scale.scale_mm_per_pt
    candidate_id = _resolve_candidate_id(semantic, candidates)
    candidate = _get_candidate(candidates, candidate_id) if candidate_id else None

    outer: list[GeometryPrimitive] = []
    hole_primitives: list[GeometryPrimitive] = []
    hole_classifications: list[HoleClassification] = []
    secondary_ops: list[dict] = list(features.secondary_operations)

    if profile is not None and candidate_id == "main_boundary":
        outer = loop_to_primitives(
            profile.outer,
            scale_k,
            "outer",
            region_id=candidate.region_id if candidate else "main_view",
        )
    elif candidate:
        chain_map = {ca.chain.id: ca for ca in chains.chains}
        hole_chain_ids = {
            int(cid.replace("chain_", ""))
            for hf in features.hole_features
            for cid in hf.candidate_geometry_ids
            if cid.startswith("chain_")
        }

        for chain_id in candidate.chain_ids:
            ca = chain_map.get(chain_id)
            if ca is None:
                continue
            # Restrict to main view bbox when available
            if main_view_bbox and ca.bbox:
                cx = (ca.bbox[0] + ca.bbox[2]) / 2
                cy = (ca.bbox[1] + ca.bbox[3]) / 2
                if not (
                    main_view_bbox[0] <= cx <= main_view_bbox[2]
                    and main_view_bbox[1] <= cy <= main_view_bbox[3]
                ):
                    continue
            # Skip hole circles from outer profile
            if chain_id in hole_chain_ids:
                continue
            if ca.fitted_circle and ca.chain.closed and ca.fitted_circle.radius_pt < 15:
                continue
            outer.extend(
                _chain_to_primitives(ca, scale_k, f"outer_{chain_id}", region_id=candidate.region_id)
            )

    # Hole features from deterministic grouping
    for hf in features.hole_features:
        if hf.cut_circle:
            hole_primitives.append(hf.cut_circle)
        if hf.through_diameter_mm:
            hole_classifications.append(
                HoleClassification(
                    id=hf.id,
                    center=hf.center,
                    diameter_mm=hf.through_diameter_mm,
                    operation="cut",
                    confidence=Confidence.MEDIUM,
                    candidate_ids=hf.candidate_geometry_ids,
                )
            )

    secondary_ops = _merge_semantic_secondary_ops(secondary_ops, semantic)

    for h in semantic.holes:
        if h.operation == "cut" and not any(
            abs(hc.diameter_mm - h.diameter_mm) < 0.5 for hc in hole_classifications
        ):
            hole_classifications.append(h)

    bbox = _compute_bbox_mm(outer)

    return PartDefinition(
        part_number=tb.part_number,
        part_name=tb.part_name,
        material=tb.material,
        thickness_mm=tb.thickness_mm,
        scale=tb.scale,
        outer_contour=outer,
        internal_features=hole_primitives,
        holes=hole_classifications,
        secondary_operations=secondary_ops,
        source_contour_id=candidate_id,
        source_region_id=candidate.region_id if candidate else None,
        scale_mm_per_pt=scale_k,
        confidence=ConfidenceBundle(
            geometry_extraction=0.8 if outer else 0.0,
            topology_chaining=0.7 if candidate else 0.0,
            dimension_association=scale.consensus_confidence,
            semantic_classification=0.7 if semantic.provider != "heuristic" else 0.3,
            validation=0.0,
        ),
        bbox_mm=bbox,
    )
