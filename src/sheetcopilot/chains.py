"""Endpoint chaining, cleanup, and conservative arc/circle fitting."""

from __future__ import annotations

import math
from collections import defaultdict

from sheetcopilot.config import TOLERANCES
from sheetcopilot.models import (
    Chain,
    ChainAnalysis,
    ChainPoint,
    ChainsResult,
    ClassifiedResult,
    FittedArc,
    FittedCircle,
    Point2D,
    PrimitiveCategory,
    Segment,
    VectorExtraction,
)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _chain_length(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(1, len(points)):
        total += _dist(points[i - 1], points[i])
    return total


def snap_endpoints(segments: list[Segment], tol: float | None = None) -> list[Segment]:
    """Snap nearby endpoints to a common grid."""
    tol = tol or TOLERANCES.snap_tol_pt
    if not segments:
        return segments

    points: list[tuple[float, float]] = []
    for s in segments:
        points.append((s.start.x, s.start.y))
        points.append((s.end.x, s.end.y))

    # Simple grid snap
    snapped: list[Segment] = []
    for s in segments:
        def snap_pt(x: float, y: float) -> tuple[float, float]:
            for px, py in points:
                if _dist((x, y), (px, py)) <= tol and _dist((x, y), (px, py)) > 0:
                    return px, py
            return x, y

        sx, sy = snap_pt(s.start.x, s.start.y)
        ex, ey = snap_pt(s.end.x, s.end.y)
        snapped.append(
            s.model_copy(
                update={
                    "start": Point2D(x=sx, y=sy),
                    "end": Point2D(x=ex, y=ey),
                }
            )
        )
    return snapped


def remove_degenerate(segments: list[Segment]) -> list[Segment]:
    """Remove zero-length and near-duplicate segments."""
    min_len = TOLERANCES.min_segment_pt
    seen: set[tuple] = set()
    result: list[Segment] = []
    for s in segments:
        length = _dist((s.start.x, s.start.y), (s.end.x, s.end.y))
        if length < min_len:
            continue
        key = (
            round(min(s.start.x, s.end.x), 2),
            round(min(s.start.y, s.end.y), 2),
            round(max(s.start.x, s.end.x), 2),
            round(max(s.start.y, s.end.y), 2),
            round(s.stroke_width, 3),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(s)
    return result


def filter_eligible_segments(classified: ClassifiedResult) -> list[Segment]:
    """Only manufacturing_candidate segments enter chaining."""
    eligible_cats = {
        PrimitiveCategory.MANUFACTURING_CANDIDATE,
        PrimitiveCategory.UNKNOWN,  # allow unknown in main view for now
    }
    return [s for s in classified.segments if s.category in eligible_cats]


def chain_segments(
    segments: list[Segment],
    tol_pt: float | None = None,
    max_gap_pt: float | None = None,
) -> list[Chain]:
    """Chain segments with matching stroke width by endpoint proximity."""
    tol_pt = tol_pt or TOLERANCES.chain_tol_pt
    max_gap = max_gap_pt or TOLERANCES.max_chain_gap_pt

    if not segments:
        return []

    by_width: dict[float, list[tuple[int, Segment]]] = defaultdict(list)
    for idx, seg in enumerate(segments):
        w = round(seg.stroke_width, 3)
        by_width[w].append((idx, seg))

    chains: list[Chain] = []
    chain_id = 0

    for stroke_width, segs in by_width.items():
        used = [False] * len(segs)

        for i, (_, start_seg) in enumerate(segs):
            if used[i]:
                continue
            used[i] = True
            points: list[tuple[float, float]] = [
                (start_seg.start.x, start_seg.start.y),
                (start_seg.end.x, start_seg.end.y),
            ]
            seg_ids = [start_seg.id]
            region_id = start_seg.region_id

            for end_idx in (0, -1):
                while True:
                    tip = points[0] if end_idx == 0 else points[-1]
                    found = None
                    for j, (_, seg) in enumerate(segs):
                        if used[j]:
                            continue
                        for attach_pt, new_pt in [
                            ((seg.start.x, seg.start.y), (seg.end.x, seg.end.y)),
                            ((seg.end.x, seg.end.y), (seg.start.x, seg.start.y)),
                        ]:
                            d = _dist(tip, attach_pt)
                            if d <= tol_pt:
                                found = (j, new_pt, seg.id)
                                break
                        if found:
                            break
                    if not found:
                        break
                    j, new_pt, sid = found
                    # Gap guard — don't bridge large gaps
                    gap = _dist(tip, new_pt)
                    if gap > max_gap and len(points) > 2:
                        break
                    used[j] = True
                    if end_idx == 0:
                        points.insert(0, new_pt)
                    else:
                        points.append(new_pt)
                    seg_ids.append(sid)

            closed = _dist(points[0], points[-1]) <= tol_pt
            if closed and len(points) > 2:
                points = points[:-1]

            chains.append(
                Chain(
                    id=chain_id,
                    points=[ChainPoint(x=p[0], y=p[1]) for p in points],
                    stroke_width=stroke_width,
                    closed=closed,
                    length_pt=_chain_length(points + ([points[0]] if closed else [])),
                    segment_ids=seg_ids,
                    region_id=region_id,
                )
            )
            chain_id += 1

    return chains


def fit_circle(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Least-squares circle fit. Returns (cx, cy, R, max_deviation) or None."""
    n = len(points)
    if n < 3:
        return None

    sx = sy = sxx = syy = sxy = sz = szx = szy = 0.0
    for x, y in points:
        z = x * x + y * y
        sx += x
        sy += y
        sxx += x * x
        syy += y * y
        sxy += x * y
        sz += z
        szx += z * x
        szy += z * y

    def det3(m: list[list[float]]) -> float:
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    a_mat = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, float(n)]]
    b_vec = [-szx, -szy, -sz]
    d0 = det3(a_mat)
    if abs(d0) < 1e-12:
        return None

    sol = []
    for col in range(3):
        m = [row[:] for row in a_mat]
        for r in range(3):
            m[r][col] = b_vec[r]
        sol.append(det3(m) / d0)

    dc, ec, fc = sol
    cx, cy = -dc / 2, -ec / 2
    v = cx * cx + cy * cy - fc
    if v <= 0:
        return None
    r = math.sqrt(v)
    max_dev = max(abs(math.hypot(x - cx, y - cy) - r) for x, y in points)
    return cx, cy, r, max_dev


def analyze_chain(chain: Chain) -> ChainAnalysis:
    pts = [(p.x, p.y) for p in chain.points]
    bbox = None
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bbox = (min(xs), min(ys), max(xs), max(ys))

    fitted_arc = None
    fitted_circle = None
    is_circular = False
    circularity_ratio = 0.0

    min_pts = TOLERANCES.arc_fit_min_points
    max_dev_ratio = TOLERANCES.arc_fit_max_deviation_ratio

    if len(pts) >= min_pts:
        fit = fit_circle(pts)
        if fit:
            cx, cy, r, max_dev = fit
            circularity_ratio = max_dev / r if r > 0 else 1.0
            is_circular = circularity_ratio < max_dev_ratio
            confidence = max(0.0, 1.0 - circularity_ratio / max_dev_ratio)

            if chain.closed or _dist(pts[0], pts[-1]) < r * 0.05:
                if is_circular:
                    fitted_circle = FittedCircle(
                        center=Point2D(x=cx, y=cy),
                        radius_pt=r,
                        max_deviation_pt=max_dev,
                        confidence=confidence,
                        source_segment_ids=chain.segment_ids,
                    )
            else:
                if is_circular or (r > 50 and circularity_ratio < 0.08):
                    start_angle = math.degrees(math.atan2(pts[0][1] - cy, pts[0][0] - cx))
                    end_angle = math.degrees(math.atan2(pts[-1][1] - cy, pts[-1][0] - cx))
                    fitted_arc = FittedArc(
                        center=Point2D(x=cx, y=cy),
                        radius_pt=r,
                        max_deviation_pt=max_dev,
                        start_angle_deg=start_angle,
                        end_angle_deg=end_angle,
                        confidence=confidence,
                        source_segment_ids=chain.segment_ids,
                    )

    return ChainAnalysis(
        chain=chain,
        is_circular=is_circular,
        circularity_ratio=circularity_ratio,
        fitted_arc=fitted_arc,
        fitted_circle=fitted_circle,
        bbox=bbox,
    )


def run_chains_from_classified(classified: ClassifiedResult, page_width: float, page_height: float) -> ChainsResult:
    """Chain only eligible (manufacturing) segments."""
    eligible = filter_eligible_segments(classified)
    cleaned = remove_degenerate(snap_endpoints(eligible))
    raw_chains = chain_segments(cleaned)
    analyses = [analyze_chain(c) for c in raw_chains]
    return ChainsResult(
        chains=analyses,
        page_width_pt=page_width,
        page_height_pt=page_height,
    )


def run_chains(vector: VectorExtraction) -> ChainsResult:
    """Legacy entry — chains all segments (prefer run_chains_from_classified)."""
    cleaned = remove_degenerate(snap_endpoints(vector.segments))
    raw_chains = chain_segments(cleaned)
    analyses = [analyze_chain(c) for c in raw_chains]
    return ChainsResult(
        chains=analyses,
        page_width_pt=vector.page_width_pt,
        page_height_pt=vector.page_height_pt,
    )
