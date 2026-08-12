"""Candidate contour generation, metrics, and deterministic ranking."""

from __future__ import annotations

import math

from sheetcopilot.config import TOLERANCES
from sheetcopilot.models import (
    CandidateContour,
    CandidatesResult,
    ChainAnalysis,
    ChainsResult,
    PrimitiveCategory,
    RegionsResult,
    Segment,
)
from sheetcopilot.loops import PartProfile
from sheetcopilot.regions import get_region


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _distance_from_page_edge(bbox: tuple[float, float, float, float], pw: float, ph: float) -> float:
    return min(bbox[0], bbox[1], pw - bbox[2], ph - bbox[3])


def is_page_frame_candidate(
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> bool:
    page_area = page_width * page_height
    area = _bbox_area(bbox)
    if page_area <= 0:
        return False
    ratio = area / page_area
    tol = TOLERANCES.page_frame_edge_margin_pt
    hugs = (
        bbox[0] <= tol
        and bbox[1] <= tol
        and bbox[2] >= page_width - tol
        and bbox[3] >= page_height - tol
    )
    return ratio >= TOLERANCES.page_frame_area_ratio or (ratio > 0.7 and hugs)


def build_candidate_from_chain(
    ca: ChainAnalysis,
    cid: str,
    page_width: float,
    page_height: float,
    region_id: str | None,
) -> CandidateContour | None:
    if not ca.bbox:
        return None
    bbox = ca.bbox
    area = _bbox_area(bbox)
    if area < 500:
        return None

    is_frame = is_page_frame_candidate(bbox, page_width, page_height)
    page_pct = area / (page_width * page_height) * 100 if page_width * page_height > 0 else 0

    arc_count = 1 if ca.fitted_arc else 0
    circle_count = 1 if ca.fitted_circle and ca.chain.closed else 0
    line_count = max(0, len(ca.chain.points) - 1 - arc_count - circle_count)

    return CandidateContour(
        id=cid,
        chain_ids=[ca.chain.id],
        bbox=bbox,
        area_pt2=area,
        stroke_width=ca.chain.stroke_width,
        is_closed=ca.chain.closed or ca.fitted_circle is not None,
        region_id=region_id,
        perimeter_pt=ca.chain.length_pt,
        line_count=line_count,
        arc_count=arc_count,
        circle_count=circle_count,
        page_area_pct=page_pct,
        distance_from_page_edge_pt=_distance_from_page_edge(bbox, page_width, page_height),
        is_page_frame=is_frame,
    )


def build_main_boundary_candidate(
    chains: ChainsResult,
    regions: RegionsResult,
    segments: list[Segment],
    profile: PartProfile | None = None,
) -> CandidateContour | None:
    """
    Candidate for the part outline, taken from the closed profile loop.

    Membership comes from endpoint connectivity, so dimension lines, extension
    lines, centre marks and section hatching that merely overlap the main view
    are excluded rather than filtered by position afterwards.
    """
    if profile is None:
        return None

    loop = profile.outer
    bbox = loop.bbox
    area = _bbox_area(bbox)
    pw, ph = chains.page_width_pt, chains.page_height_pt

    return CandidateContour(
        id="main_boundary",
        chain_ids=[],
        bbox=bbox,
        area_pt2=area,
        stroke_width=loop.stroke_width,
        is_closed=loop.closed,
        region_id="main_view" if regions.main_view_id else None,
        perimeter_pt=loop.perimeter_pt,
        line_count=len(loop.segments),
        arc_count=0,
        circle_count=0,
        page_area_pct=area / (pw * ph) * 100 if pw * ph > 0 else 0,
        distance_from_page_edge_pt=_distance_from_page_edge(bbox, pw, ph),
        is_page_frame=False,
        rank_reason=(
            f"closed profile loop of {len(loop.segments)} segments "
            f"at stroke {loop.stroke_width:g}pt"
        ),
    )


def rank_candidates(
    candidates: list[CandidateContour],
    regions: RegionsResult,
    scale_mm_per_pt: float,
    annotated_widths: list[float] | None = None,
) -> list[CandidateContour]:
    """Deterministic ranking — never pick largest blindly."""
    annotated_widths = annotated_widths or []
    ranked: list[CandidateContour] = []

    for c in candidates:
        score = 0.0
        reasons: list[str] = []

        if c.is_page_frame:
            score -= 1000
            reasons.append("page_frame_penalty")

        if c.region_id == "main_view":
            score += 50
            reasons.append("in_main_view")

        if c.is_closed:
            score += 10
            reasons.append("closed")

        if c.id == "main_boundary":
            score += 30
            reasons.append("main_boundary_synthesized")

        if c.arc_count > 0:
            score += 15
            reasons.append("has_arcs")

        if c.circle_count > 0 and c.area_pt2 < 5000:
            score -= 20  # small circle, likely hole not outer
            reasons.append("small_circle_penalty")

        # Prefer reasonable size relative to annotations
        if scale_mm_per_pt > 0 and annotated_widths:
            width_mm = (c.bbox[2] - c.bbox[0]) * scale_mm_per_pt
            for aw in annotated_widths:
                if aw > 100:
                    err = abs(width_mm - aw) / aw
                    if err < 0.05:
                        score += 40
                        reasons.append(f"width_matches_{aw}")
                    elif err < 0.15:
                        score += 20
                    elif err > 0.5:
                        score -= 30
                        reasons.append(f"width_mismatch_{aw}")

        # Penalize hugging page edge
        if c.distance_from_page_edge_pt < 5:
            score -= 50
            reasons.append("edge_hugging")

        # Prefer moderate page coverage (5-40%)
        if 3 < c.page_area_pct < 40:
            score += 10
        elif c.page_area_pct > 70:
            score -= 80

        ranked.append(
            c.model_copy(
                update={
                    "rank_score": score,
                    "rank_reason": "; ".join(reasons),
                }
            )
        )

    ranked.sort(key=lambda c: -c.rank_score)
    return ranked


def select_best_candidate(candidates: list[CandidateContour]) -> str | None:
    """Pick top-ranked non-frame candidate, preferring main_boundary."""
    for c in candidates:
        if c.id == "main_boundary" and not c.is_page_frame and c.rank_score > -200:
            return c.id
    for c in candidates:
        if c.is_page_frame:
            continue
        if c.rank_score < -100:
            continue
        return c.id
    return None


def run_candidates(
    chains: ChainsResult,
    regions: RegionsResult,
    segments: list[Segment],
    scale_mm_per_pt: float = 1.0,
    annotated_widths: list[float] | None = None,
    profile: PartProfile | None = None,
) -> CandidatesResult:
    """Build, rank, and select candidate contours."""
    pw, ph = chains.page_width_pt, chains.page_height_pt
    candidates: list[CandidateContour] = []

    # Individual chain candidates in main view
    cid = 0
    for ca in chains.chains:
        ch = ca.chain
        if ch.length_pt < 30:
            continue
        region_id = ch.region_id
        if region_id in ("page_frame", "title_block"):
            continue
        cand = build_candidate_from_chain(ca, f"contour_{cid}", pw, ph, region_id)
        if cand and not cand.is_page_frame:
            candidates.append(cand)
            cid += 1

    # Profile-loop boundary
    boundary = build_main_boundary_candidate(chains, regions, segments, profile)
    if boundary:
        candidates.append(boundary)

    ranked = rank_candidates(candidates, regions, scale_mm_per_pt, annotated_widths)
    selected = select_best_candidate(ranked)

    return CandidatesResult(
        candidates=ranked,
        selected_id=selected,
    )
