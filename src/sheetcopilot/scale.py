"""Multi-anchor scale consensus."""

from __future__ import annotations

import math
import re
import statistics

from sheetcopilot.config import TOLERANCES
from sheetcopilot.models import (
    ChainAnalysis,
    ChainsResult,
    DimensionCandidate,
    DimensionType,
    DimensionsResult,
    DrawingRegion,
    RegionsResult,
    ScaleAnchor,
    ScaleResult,
    Segment,
    TextSpan,
)
from sheetcopilot.regions import get_region
from sheetcopilot.vector import parse_german_number


def parse_scale_ratio(scale_hint: str | None) -> float | None:
    if not scale_hint:
        return None
    m = re.match(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", scale_hint)
    if not m:
        return None
    drawing, real = float(m.group(1)), float(m.group(2))
    if drawing <= 0:
        return None
    return real / drawing


def _scale_prior_from_title(texts: list[TextSpan]) -> float | None:
    """Nominal mm/pt from title block scale hint (e.g. 1:2.5 on A3)."""
    for span in texts:
        m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", span.text)
        if m:
            ratio = float(m.group(2)) / float(m.group(1))
            return (25.4 / 72.0) * ratio
    return None


def _main_view_extent_pt(
    segments: list[Segment],
    regions: RegionsResult,
) -> tuple[float, float] | None:
    """Measure main view width and height in pt from eligible geometry."""
    main = get_region(regions, "main_view") if regions.main_view_id else None
    if not main:
        return None

    xs: list[float] = []
    ys: list[float] = []
    for seg in segments:
        if seg.category.value not in ("manufacturing_candidate", "unknown"):
            continue
        cx = (seg.start.x + seg.end.x) / 2
        cy = (seg.start.y + seg.end.y) / 2
        if main.bbox[0] <= cx <= main.bbox[2] and main.bbox[1] <= cy <= main.bbox[3]:
            xs.extend([seg.start.x, seg.end.x])
            ys.extend([seg.start.y, seg.end.y])

    if len(xs) < 4:
        return None
    return max(xs) - min(xs), max(ys) - min(ys)


def build_scale_anchors(
    dimensions: DimensionsResult,
    segments: list[Segment],
    chains: ChainsResult,
    regions: RegionsResult,
    texts: list[TextSpan],
) -> list[ScaleAnchor]:
    """Build independent scale estimates from annotated dimensions."""
    anchors: list[ScaleAnchor] = []
    main = get_region(regions, "main_view") if regions.main_view_id else None

    # Linear anchors from main view extent
    extent = _main_view_extent_pt(segments, regions)
    if extent and main:
        width_pt, height_pt = extent
        for dim in dimensions.dimensions:
            if dim.dim_type != DimensionType.LINEAR:
                continue
            if dim.value_mm < 100 or dim.value_mm > 2000:
                continue  # skip spurious values
            for measured_pt, label in [(width_pt, "width"), (height_pt, "height")]:
                if measured_pt <= 0:
                    continue
                k = dim.value_mm / measured_pt
                anchors.append(
                    ScaleAnchor(
                        annotation_mm=dim.value_mm,
                        measured_pt=measured_pt,
                        scale_mm_per_pt=k,
                        error_percent=0.0,
                        status="accepted",
                        dim_type=f"linear_{label}",
                        dimension_id=dim.id,
                        source="main_view_extent",
                    )
                )

    # Prior from title block scale
    prior = _scale_prior_from_title(texts)
    if prior:
        anchors.append(
            ScaleAnchor(
                annotation_mm=0,
                measured_pt=0,
                scale_mm_per_pt=prior,
                error_percent=0,
                status="accepted",
                dim_type="scale_prior",
                source="title_block_scale",
            )
        )

    # Radius anchors (lower confidence — large radii are ill-conditioned)
    for dim in dimensions.dimensions:
        if dim.dim_type != DimensionType.RADIUS:
            continue
        for ca in chains.chains:
            r_pt = None
            if ca.fitted_arc and ca.fitted_arc.radius_pt >= 200:
                r_pt = ca.fitted_arc.radius_pt
            elif ca.fitted_circle and ca.fitted_circle.radius_pt >= 200:
                r_pt = ca.fitted_circle.radius_pt
            if r_pt is None:
                continue
            k = dim.value_mm / r_pt
            anchors.append(
                ScaleAnchor(
                    annotation_mm=dim.value_mm,
                    measured_pt=r_pt,
                    scale_mm_per_pt=k,
                    error_percent=0,
                    status="accepted",
                    dim_type="radius",
                    dimension_id=dim.id,
                    source="arc_fit",
                )
            )

    return anchors


def compute_scale_consensus(anchors: list[ScaleAnchor]) -> ScaleResult:
    """Consensus scale with outlier rejection."""
    prior = next((a.scale_mm_per_pt for a in anchors if a.dim_type == "scale_prior"), None)

    # Collect scale estimates (exclude prior for median)
    estimates = [a.scale_mm_per_pt for a in anchors if a.dim_type != "scale_prior" and a.measured_pt > 0]
    if not estimates and prior:
        return ScaleResult(
            scale_mm_per_pt=prior,
            anchors=anchors,
            consensus_confidence=0.5,
            stable=True,
            prior_mm_per_pt=prior,
        )
    if not estimates:
        return ScaleResult(
            scale_mm_per_pt=prior or (25.4 / 72.0) * 2.5,
            anchors=anchors,
            consensus_confidence=0.0,
            stable=False,
            prior_mm_per_pt=prior,
        )

    median_k = statistics.median(estimates)

    # Reject outliers
    updated_anchors: list[ScaleAnchor] = []
    accepted: list[float] = []
    for a in anchors:
        if a.measured_pt <= 0 and a.dim_type == "scale_prior":
            err_pct = abs(a.scale_mm_per_pt - median_k) / median_k * 100 if median_k else 0
            status: str = "accepted" if err_pct <= TOLERANCES.scale_outlier_pct * 3 else "outlier"
            updated_anchors.append(a.model_copy(update={"error_percent": err_pct, "status": status}))
            if status == "accepted":
                accepted.append(a.scale_mm_per_pt)
            continue
        if a.measured_pt <= 0:
            updated_anchors.append(a)
            continue
        err_pct = abs(a.scale_mm_per_pt - median_k) / median_k * 100 if median_k else 100
        status = "accepted" if err_pct <= TOLERANCES.scale_outlier_pct else "outlier"
        updated_anchors.append(a.model_copy(update={"error_percent": err_pct, "status": status}))
        if status == "accepted":
            accepted.append(a.scale_mm_per_pt)

    if len(accepted) >= TOLERANCES.scale_min_anchors:
        final_k = statistics.median(accepted)
        stable = True
        confidence = min(0.99, 0.5 + len(accepted) * 0.15)
    elif accepted:
        final_k = statistics.median(accepted)
        stable = len(accepted) >= 1
        confidence = 0.5
    elif prior and estimates:
        # Prior agrees with at least one extent anchor within tolerance
        close = [e for e in estimates if abs(e - prior) / prior * 100 <= TOLERANCES.scale_outlier_pct * 2]
        if close:
            final_k = statistics.median(close + [prior])
            stable = True
            confidence = 0.6
        else:
            final_k = prior
            stable = False
            confidence = 0.3
    elif prior:
        final_k = prior
        stable = True  # title-block scale prior is acceptable when no conflicting anchors
        confidence = 0.5
    else:
        final_k = median_k
        stable = False
        confidence = 0.2

    return ScaleResult(
        scale_mm_per_pt=final_k,
        anchors=updated_anchors,
        consensus_confidence=confidence,
        stable=stable,
        prior_mm_per_pt=prior,
    )


def run_scale(
    dimensions: DimensionsResult,
    segments: list[Segment],
    chains: ChainsResult,
    regions: RegionsResult,
    texts: list[TextSpan],
) -> ScaleResult:
    anchors = build_scale_anchors(dimensions, segments, chains, regions, texts)
    return compute_scale_consensus(anchors)
