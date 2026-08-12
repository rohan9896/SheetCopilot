"""Dimension parsing and feature association."""

from __future__ import annotations

import math
import re

from sheetcopilot.config import TOLERANCES
from sheetcopilot.models import (
    ChainAnalysis,
    ChainsResult,
    DimensionCandidate,
    DimensionType,
    DimensionsResult,
    Segment,
    TextSpan,
)
from sheetcopilot.vector import extract_diameter_mm, extract_radius_mm, parse_german_number


def _text_center(span: TextSpan) -> tuple[float, float]:
    return ((span.x0 + span.x1) / 2, (span.y0 + span.y1) / 2)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def parse_dimensions(texts: list[TextSpan]) -> list[DimensionCandidate]:
    """Extract typed dimension candidates from drawing text."""
    dims: list[DimensionCandidate] = []
    seen_values: set[tuple[str, float]] = set()
    dim_id = 0

    for span in texts:
        t = span.text.strip()
        if not t:
            continue

        # Skip scale, dates, part numbers, and part-name tokens with units
        if re.search(r"^\d:\d", t) or re.search(r"^\d{2}\.\d{2}\.\d{4}", t):
            continue
        if re.search(r"^[A-Z]{2,}\s+\d", t):
            continue
        if re.search(r"\d+[.,]\d+\s*m\b", t, re.IGNORECASE):
            continue

        dim_type: DimensionType | None = None
        value: float | None = None

        r = extract_radius_mm(t)
        if r is not None and r >= 50:
            dim_type = DimensionType.RADIUS
            value = r
        else:
            d = extract_diameter_mm(t)
            if d is not None and 2 <= d <= 200:
                dim_type = DimensionType.HOLE_DIAMETER if d <= 50 else DimensionType.DIAMETER
                value = d
            else:
                # Standalone hole callouts like "18" or "34" near title block
                val = parse_german_number(t)
                if val is not None and val in (18, 20, 22, 25, 30, 34):
                    dim_type = DimensionType.HOLE_DIAMETER
                    value = float(val)
                elif val is not None and 1 <= val <= 10000:
                    dim_type = DimensionType.LINEAR
                    value = val

        if dim_type is None or value is None:
            continue

        key = (dim_type.value, round(value, 2))
        if key in seen_values:
            continue
        seen_values.add(key)

        dims.append(
            DimensionCandidate(
                id=f"dim_{dim_id}",
                value_mm=value,
                dim_type=dim_type,
                text=t,
                text_bbox=(span.x0, span.y0, span.x1, span.y1),
            )
        )
        dim_id += 1

    return dims


def associate_dimensions(
    dimensions: list[DimensionCandidate],
    segments: list[Segment],
    chains: ChainsResult,
    scale_mm_per_pt: float | None = None,
) -> list[DimensionCandidate]:
    """Link dimension text to nearest geometry."""
    updated: list[DimensionCandidate] = []

    for dim in dimensions:
        tcx, tcy = _text_center_from_bbox(dim.text_bbox)
        refs: list[str] = []
        confidence = 0.0

        if dim.dim_type in (DimensionType.LINEAR, DimensionType.OVERALL_WIDTH, DimensionType.OVERALL_HEIGHT):
            best_len: float | None = None
            best_dist = float("inf")
            for seg in segments:
                if seg.category.value in ("annotation_symbol", "border", "title_block"):
                    continue
                mid = ((seg.start.x + seg.end.x) / 2, (seg.start.y + seg.end.y) / 2)
                d = _dist((tcx, tcy), mid)
                if d > TOLERANCES.dimension_text_proximity_pt:
                    continue
                seg_len = _dist((seg.start.x, seg.start.y), (seg.end.x, seg.end.y))
                if scale_mm_per_pt:
                    seg_len_mm = seg_len * scale_mm_per_pt
                    err = abs(seg_len_mm - dim.value_mm) / dim.value_mm
                    if err < 0.1 and d < best_dist:
                        best_dist = d
                        best_len = seg_len
                        refs = [seg.id]
            if refs:
                confidence = max(0.5, 1.0 - best_dist / TOLERANCES.dimension_text_proximity_pt)

        elif dim.dim_type == DimensionType.RADIUS:
            best_err = float("inf")
            for ca in chains.chains:
                r_pt = None
                if ca.fitted_arc and ca.fitted_arc.radius_pt >= 50:
                    r_pt = ca.fitted_arc.radius_pt
                elif ca.fitted_circle and ca.fitted_circle.radius_pt >= 50:
                    r_pt = ca.fitted_circle.radius_pt
                if r_pt is None or not ca.bbox:
                    continue
                cx = (ca.bbox[0] + ca.bbox[2]) / 2
                cy = (ca.bbox[1] + ca.bbox[3]) / 2
                d = _dist((tcx, tcy), (cx, cy))
                if scale_mm_per_pt:
                    r_mm = r_pt * scale_mm_per_pt
                    err = abs(r_mm - dim.value_mm) / dim.value_mm
                    if err < best_err and d < 300:
                        best_err = err
                        refs = [f"chain_{ca.chain.id}"]
                        confidence = max(0.5, 1.0 - err)

        elif dim.dim_type in (DimensionType.DIAMETER, DimensionType.HOLE_DIAMETER):
            for ca in chains.chains:
                if not ca.fitted_circle or not ca.chain.closed:
                    continue
                if not ca.bbox:
                    continue
                cx = (ca.bbox[0] + ca.bbox[2]) / 2
                cy = (ca.bbox[1] + ca.bbox[3]) / 2
                d = _dist((tcx, tcy), (cx, cy))
                if scale_mm_per_pt and d < 80:
                    dia_mm = 2 * ca.fitted_circle.radius_pt * scale_mm_per_pt
                    err = abs(dia_mm - dim.value_mm) / dim.value_mm
                    if err < 0.15:
                        refs = [f"chain_{ca.chain.id}"]
                        confidence = max(0.6, 1.0 - err)

        updated.append(
            dim.model_copy(
                update={
                    "referenced_candidate_ids": refs,
                    "association_confidence": confidence,
                }
            )
        )
    return updated


def _text_center_from_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def run_dimensions(
    texts: list[TextSpan],
    segments: list[Segment],
    chains: ChainsResult,
    scale_mm_per_pt: float | None = None,
) -> DimensionsResult:
    """Parse and associate dimensions."""
    dims = parse_dimensions(texts)
    dims = associate_dimensions(dims, segments, chains, scale_mm_per_pt)

    linear = sorted({d.value_mm for d in dims if d.dim_type == DimensionType.LINEAR})
    radii = sorted({d.value_mm for d in dims if d.dim_type == DimensionType.RADIUS}, reverse=True)
    diameters = sorted(
        {d.value_mm for d in dims if d.dim_type in (DimensionType.DIAMETER, DimensionType.HOLE_DIAMETER)}
    )

    return DimensionsResult(
        dimensions=dims,
        annotated_linear_mm=linear,
        annotated_radii_mm=radii,
        annotated_diameters_mm=diameters,
    )
