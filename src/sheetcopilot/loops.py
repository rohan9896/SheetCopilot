"""
Connectivity-based loop extraction.

Contour selection is driven by endpoint connectivity rather than bounding-box
containment. A drawing's part profile is a closed loop of stroked segments;
dimension lines, extension lines, centre marks and hatching are not connected to
it, so they fall out naturally instead of needing to be filtered by position.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sheetcopilot.config import TOLERANCES
from sheetcopilot.models import GeometryPrimitive, Point2D, Segment


@dataclass
class GeometryLoop:
    """A connected run of segments, ordered head-to-tail."""

    id: str
    segments: list[Segment]
    points: list[tuple[float, float]]
    closed: bool
    bbox: tuple[float, float, float, float]
    area_pt2: float
    perimeter_pt: float
    stroke_width: float
    segment_ids: list[str] = field(default_factory=list)

    @property
    def width_pt(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height_pt(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def center(self) -> tuple[float, float]:
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)


def _grid_key(x: float, y: float, tol: float) -> tuple[int, int]:
    return (int(round(x / tol)), int(round(y / tol)))


def connected_components(
    segments: list[Segment],
    tol: float | None = None,
) -> list[list[Segment]]:
    """Group segments into components joined at shared endpoints."""
    tol = tol or TOLERANCES.loop_join_tol_pt
    if not segments:
        return []

    parent = list(range(len(segments)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, seg in enumerate(segments):
        for x, y in ((seg.start.x, seg.start.y), (seg.end.x, seg.end.y)):
            buckets[_grid_key(x, y, tol)].append(i)

    for (gx, gy), idxs in buckets.items():
        first = idxs[0]
        for j in idxs[1:]:
            union(first, j)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                for j in buckets.get((gx + dx, gy + dy), ()):
                    union(first, j)

    grouped: dict[int, list[Segment]] = defaultdict(list)
    for i, seg in enumerate(segments):
        grouped[find(i)].append(seg)
    return list(grouped.values())


def trace_paths(
    segments: list[Segment],
    tol: float | None = None,
) -> list[tuple[list[tuple[float, float]], bool]]:
    """
    Order a component into head-to-tail paths covering every segment.

    A component may branch where a leader or centre mark meets a circle, so
    tracing continues from the remaining free ends until nothing is left.
    """
    tol = tol or TOLERANCES.loop_join_tol_pt
    if not segments:
        return []

    adjacency: dict[tuple[int, int], list[tuple[int, tuple[float, float]]]] = defaultdict(list)
    for i, seg in enumerate(segments):
        a = (seg.start.x, seg.start.y)
        b = (seg.end.x, seg.end.y)
        adjacency[_grid_key(*a, tol)].append((i, b))
        adjacency[_grid_key(*b, tol)].append((i, a))

    degree = {key: len(entries) for key, entries in adjacency.items()}
    used: set[int] = set()
    paths: list[tuple[list[tuple[float, float]], bool]] = []

    def walk(start: tuple[float, float]) -> list[tuple[float, float]]:
        points = [start]
        current = start
        while True:
            nxt: tuple[int, tuple[float, float]] | None = None
            for i, other in adjacency[_grid_key(*current, tol)]:
                if i not in used:
                    nxt = (i, other)
                    break
            if nxt is None:
                break
            used.add(nxt[0])
            current = nxt[1]
            points.append(current)
        return points

    # Free ends first so open runs are captured whole, then remaining cycles.
    starts = [
        (seg.start.x, seg.start.y)
        for seg in segments
        if degree.get(_grid_key(seg.start.x, seg.start.y, tol), 0) == 1
    ] + [
        (seg.end.x, seg.end.y)
        for seg in segments
        if degree.get(_grid_key(seg.end.x, seg.end.y, tol), 0) == 1
    ]
    for start in starts:
        if len(used) == len(segments):
            break
        pts = walk(start)
        if len(pts) >= 2:
            paths.append((pts, False))

    for i, seg in enumerate(segments):
        if len(used) == len(segments):
            break
        if i in used:
            continue
        pts = walk((seg.start.x, seg.start.y))
        if len(pts) < 2:
            continue
        closed = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= tol
        paths.append((pts, closed))

    return paths


def trace_component(
    segments: list[Segment],
    tol: float | None = None,
) -> tuple[list[tuple[float, float]], bool]:
    """Order a component head-to-tail, returning its longest path."""
    paths = trace_paths(segments, tol)
    if not paths:
        return [], False
    return max(paths, key=lambda p: len(p[0]))


def polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(len(points)):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def _polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(
        math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
        for i in range(len(points) - 1)
    )


def build_loops(
    segments: list[Segment],
    tol: float | None = None,
    min_segments: int = 1,
) -> list[GeometryLoop]:
    """Build ordered loops from segments, largest enclosed area first."""
    loops: list[GeometryLoop] = []
    for idx, comp in enumerate(connected_components(segments, tol)):
        if len(comp) < min_segments:
            continue
        points, closed = trace_component(comp, tol)
        if len(points) < 2:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        widths = Counter(round(s.stroke_width, 3) for s in comp)
        loops.append(
            GeometryLoop(
                id=f"loop_{idx}",
                segments=comp,
                points=points,
                closed=closed,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                area_pt2=polygon_area(points) if closed else 0.0,
                perimeter_pt=_polyline_length(points),
                stroke_width=widths.most_common(1)[0][0],
                segment_ids=[s.id for s in comp if s.id],
            )
        )

    loops.sort(key=lambda lp: (-lp.area_pt2, -lp.perimeter_pt))
    for rank, loop in enumerate(loops):
        loop.id = f"loop_{rank}"
    return loops


def group_by_stroke_width(segments: list[Segment]) -> dict[float, list[Segment]]:
    """Bucket segments by stroke width.

    Profile, dimension and centre-mark strokes are drawn at different widths, so
    bucketing first keeps a dimension line that touches the profile from merging
    into the same component.
    """
    buckets: dict[float, list[Segment]] = defaultdict(list)
    for seg in segments:
        buckets[round(seg.stroke_width, 3)].append(seg)
    return dict(buckets)


def find_closed_loops(
    segments: list[Segment],
    page_width: float,
    page_height: float,
    min_area_ratio: float | None = None,
    tol: float | None = None,
) -> list[GeometryLoop]:
    """
    Closed, non-sheet-border loops, largest enclosed area first.

    Loops are built independently per stroke width so a dimension line touching
    the profile cannot merge into the same component.
    """
    page_area = max(page_width * page_height, 1.0)
    ratio = TOLERANCES.min_profile_area_ratio if min_area_ratio is None else min_area_ratio
    min_area = page_area * ratio

    result: list[GeometryLoop] = []
    for width, group in group_by_stroke_width(segments).items():
        for loop in build_loops(group, tol):
            if not loop.closed or loop.area_pt2 < min_area:
                continue
            if is_page_frame_loop(loop, page_width, page_height):
                continue
            loop.stroke_width = width
            result.append(loop)

    result.sort(key=lambda lp: -lp.area_pt2)
    for rank, loop in enumerate(result):
        loop.id = f"loop_{rank}"
    return result


def find_profile_loops(
    segments: list[Segment],
    page_width: float,
    page_height: float,
    tol: float | None = None,
) -> list[GeometryLoop]:
    """Candidate part-profile loops, largest enclosed area first."""
    return find_closed_loops(segments, page_width, page_height, tol=tol)


def point_in_loop(x: float, y: float, loop: GeometryLoop) -> bool:
    """Ray-cast containment test against a closed loop."""
    pts = loop.points
    if len(pts) < 3:
        return False
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def loop_inside(inner: GeometryLoop, outer: GeometryLoop) -> bool:
    """True when the inner loop lies wholly within the outer loop."""
    if inner is outer:
        return False
    ib, ob = inner.bbox, outer.bbox
    if ib[0] < ob[0] or ib[1] < ob[1] or ib[2] > ob[2] or ib[3] > ob[3]:
        return False
    cx, cy = inner.center
    return point_in_loop(cx, cy, outer)


def is_page_frame_loop(
    loop: GeometryLoop,
    page_width: float,
    page_height: float,
) -> bool:
    """Sheet borders hug the page perimeter and cover most of its area."""
    page_area = page_width * page_height
    if page_area <= 0:
        return False
    bbox_area = loop.width_pt * loop.height_pt
    margin = page_width * TOLERANCES.page_frame_margin_frac
    hugs = (
        loop.bbox[0] <= margin
        and loop.bbox[1] <= margin
        and loop.bbox[2] >= page_width - margin
        and loop.bbox[3] >= page_height - margin
    )
    return hugs or bbox_area / page_area >= TOLERANCES.page_frame_area_ratio


@dataclass
class CircleFeature:
    """A full or partial circle recovered from stroked geometry."""

    center: tuple[float, float]
    radius_pt: float
    coverage_deg: float
    max_deviation_pt: float
    segment_ids: list[str] = field(default_factory=list)
    closed: bool = False

    @property
    def diameter_pt(self) -> float:
        return 2 * self.radius_pt


def _angular_coverage(points: list[tuple[float, float]], cx: float, cy: float) -> float:
    angles = sorted(math.atan2(y - cy, x - cx) for x, y in points)
    if len(angles) < 2:
        return 0.0
    gaps = [angles[i + 1] - angles[i] for i in range(len(angles) - 1)]
    gaps.append(angles[0] + 2 * math.pi - angles[-1])
    return math.degrees(2 * math.pi - max(gaps))


def find_circle_features(
    segments: list[Segment],
    tol: float | None = None,
    min_radius_pt: float = 2.0,
    max_radius_pt: float = 120.0,
) -> list[CircleFeature]:
    """
    Recover circles from stroked geometry, including circles drawn as separate arcs.

    Each component is traced, split at corners, and every smooth run is circle
    fitted. Runs sharing a centre and radius are merged, so a ring drawn as two
    half arcs is reported once with its combined angular coverage.
    """
    from sheetcopilot.chains import fit_circle

    tol = tol or TOLERANCES.loop_join_tol_pt
    raw: list[CircleFeature] = []

    for comp in connected_components(segments, tol):
        for pts, closed in trace_paths(comp, tol):
            for run in split_at_corners(pts, closed, corner_deg=TOLERANCES.arc_corner_angle_deg):
                if len(run) < TOLERANCES.arc_fit_min_points:
                    continue
                fit = fit_circle(run)
                if not fit:
                    continue
                cx, cy, radius, dev = fit
                if not (min_radius_pt <= radius <= max_radius_pt):
                    continue
                if dev / radius > TOLERANCES.circle_fit_max_deviation_ratio:
                    continue
                on_circle = [
                    s.id
                    for s in comp
                    if s.id
                    and abs(
                        math.hypot(
                            (s.start.x + s.end.x) / 2 - cx, (s.start.y + s.end.y) / 2 - cy
                        )
                        - radius
                    )
                    <= max(dev, tol)
                ]
                raw.append(
                    CircleFeature(
                        center=(cx, cy),
                        radius_pt=radius,
                        coverage_deg=_angular_coverage(run, cx, cy),
                        max_deviation_pt=dev,
                        segment_ids=on_circle,
                        closed=closed,
                    )
                )

    merged: list[CircleFeature] = []
    for feat in sorted(raw, key=lambda f: -f.coverage_deg):
        match = None
        for existing in merged:
            same_centre = math.hypot(
                feat.center[0] - existing.center[0], feat.center[1] - existing.center[1]
            ) <= TOLERANCES.hole_concentric_tol_pt
            same_radius = abs(feat.radius_pt - existing.radius_pt) <= max(
                0.5, existing.radius_pt * 0.04
            )
            if same_centre and same_radius:
                match = existing
                break
        if match is None:
            merged.append(feat)
        else:
            match.coverage_deg = min(360.0, match.coverage_deg + feat.coverage_deg)
            match.segment_ids = sorted(set(match.segment_ids) | set(feat.segment_ids))

    return merged


@dataclass
class PartProfile:
    """The manufacturing geometry of a drawing: one outer loop plus its circles."""

    outer: GeometryLoop
    circles: list[CircleFeature] = field(default_factory=list)
    alternates: list[GeometryLoop] = field(default_factory=list)
    stroke_width: float = 0.0

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.outer.bbox


def extract_part_profile(
    segments: list[Segment],
    page_width: float,
    page_height: float,
) -> PartProfile | None:
    """
    Select the part profile and the circular features it contains.

    The outer contour is the largest closed loop that is not a sheet border.
    Only geometry drawn at the same stroke width and lying inside that loop is
    considered for holes, which keeps centre marks, leaders and section hatching
    out of the manufacturing geometry.
    """
    loops = find_profile_loops(segments, page_width, page_height)
    if not loops:
        return None

    outer = loops[0]
    profile_segments = [
        s
        for s in segments
        if abs(s.stroke_width - outer.stroke_width) <= TOLERANCES.stroke_width_match_tol_pt
    ]

    circles = [
        c
        for c in find_circle_features(profile_segments)
        if c.coverage_deg >= TOLERANCES.min_circle_coverage_deg
        and point_in_loop(c.center[0], c.center[1], outer)
    ]
    circles.sort(key=lambda c: (c.center[0], c.radius_pt))

    return PartProfile(
        outer=outer,
        circles=circles,
        alternates=loops[1:],
        stroke_width=outer.stroke_width,
    )


# --- Primitive reconstruction -------------------------------------------------


def _direction(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _angle_delta(a: float, b: float) -> float:
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return d


def split_at_corners(
    points: list[tuple[float, float]],
    closed: bool,
    corner_deg: float | None = None,
) -> list[list[tuple[float, float]]]:
    """Split an ordered polyline into smooth runs separated by sharp corners."""
    corner_rad = math.radians(corner_deg or TOLERANCES.corner_angle_deg)
    if len(points) < 3:
        return [points] if len(points) >= 2 else []

    work = points[:-1] if closed and points[0] == points[-1] else points[:]
    n = len(work)
    if n < 3:
        return [points]

    corner_idx: list[int] = []
    rng = range(n) if closed else range(1, n - 1)
    for i in rng:
        prev_pt = work[(i - 1) % n]
        cur = work[i]
        nxt = work[(i + 1) % n]
        turn = abs(_angle_delta(_direction(prev_pt, cur), _direction(cur, nxt)))
        if turn >= corner_rad:
            corner_idx.append(i)

    if not corner_idx:
        return [points]

    runs: list[list[tuple[float, float]]] = []
    if closed:
        for a, b in zip(corner_idx, corner_idx[1:] + [corner_idx[0] + n]):
            run = [work[i % n] for i in range(a, b + 1)]
            if len(run) >= 2:
                runs.append(run)
    else:
        bounds = [0, *corner_idx, n - 1]
        for a, b in zip(bounds, bounds[1:]):
            run = work[a : b + 1]
            if len(run) >= 2:
                runs.append(run)
    return runs


def _total_turn(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(1, len(points) - 1):
        total += _angle_delta(
            _direction(points[i - 1], points[i]), _direction(points[i], points[i + 1])
        )
    return abs(total)


def run_to_primitives(
    run: list[tuple[float, float]],
    scale_mm_per_pt: float,
    prefix: str,
    source_ids: list[str],
    region_id: str | None,
) -> list[GeometryPrimitive]:
    """
    Convert a smooth run into a LINE or ARC.

    An arc is only emitted when the fitted circle stays within an absolute
    deviation of every sampled vertex, so a near-straight run is never distorted
    by an ill-conditioned large-radius fit.
    """
    from sheetcopilot.chains import fit_circle

    k = scale_mm_per_pt
    if len(run) < 2:
        return []

    straight = _total_turn(run) < math.radians(TOLERANCES.straight_run_total_turn_deg)
    if straight or len(run) < TOLERANCES.arc_fit_min_points:
        return [
            GeometryPrimitive(
                id=f"{prefix}_line",
                type="line",
                points=[
                    Point2D(x=run[0][0] * k, y=run[0][1] * k),
                    Point2D(x=run[-1][0] * k, y=run[-1][1] * k),
                ],
                source_pdf_object_ids=source_ids,
                region_id=region_id,
                geometry_confidence=0.95,
            )
        ]

    fit = fit_circle(run)
    if fit:
        cx, cy, radius, max_dev = fit
        if max_dev <= TOLERANCES.arc_fit_max_deviation_pt and radius > 0:
            start_angle = math.degrees(math.atan2(run[0][1] - cy, run[0][0] - cx))
            end_angle = math.degrees(math.atan2(run[-1][1] - cy, run[-1][0] - cx))
            # Store counter-clockwise, matching DXF arc convention.
            sweep = 0.0
            for i in range(len(run) - 1):
                a0 = math.atan2(run[i][1] - cy, run[i][0] - cx)
                a1 = math.atan2(run[i + 1][1] - cy, run[i + 1][0] - cx)
                sweep += _angle_delta(a0, a1)
            if sweep < 0:
                start_angle, end_angle = end_angle, start_angle
            return [
                GeometryPrimitive(
                    id=f"{prefix}_arc",
                    type="arc",
                    center=Point2D(x=cx * k, y=cy * k),
                    radius_mm=radius * k,
                    start_angle_deg=start_angle,
                    end_angle_deg=end_angle,
                    points=[
                        Point2D(x=run[0][0] * k, y=run[0][1] * k),
                        Point2D(x=run[-1][0] * k, y=run[-1][1] * k),
                    ],
                    source_pdf_object_ids=source_ids,
                    region_id=region_id,
                    geometry_confidence=max(0.5, 1.0 - max_dev),
                )
            ]

    return [
        GeometryPrimitive(
            id=f"{prefix}_line_{i}",
            type="line",
            points=[
                Point2D(x=run[i][0] * k, y=run[i][1] * k),
                Point2D(x=run[i + 1][0] * k, y=run[i + 1][1] * k),
            ],
            source_pdf_object_ids=source_ids,
            region_id=region_id,
            geometry_confidence=0.9,
        )
        for i in range(len(run) - 1)
    ]


def loop_to_primitives(
    loop: GeometryLoop,
    scale_mm_per_pt: float,
    prefix: str,
    region_id: str | None = None,
) -> list[GeometryPrimitive]:
    """Convert a loop into LINE/ARC/CIRCLE primitives in millimetres."""
    from sheetcopilot.chains import fit_circle

    k = scale_mm_per_pt
    pts = loop.points

    if loop.closed and len(pts) >= 8:
        fit = fit_circle(pts)
        if fit:
            cx, cy, radius, max_dev = fit
            if radius > 0 and max_dev / radius <= TOLERANCES.circle_fit_max_deviation_ratio:
                return [
                    GeometryPrimitive(
                        id=f"{prefix}_circle",
                        type="circle",
                        center=Point2D(x=cx * k, y=cy * k),
                        radius_mm=radius * k,
                        source_pdf_object_ids=loop.segment_ids,
                        region_id=region_id,
                        geometry_confidence=max(0.5, 1.0 - max_dev / radius),
                    )
                ]

    primitives: list[GeometryPrimitive] = []
    for i, run in enumerate(split_at_corners(pts, loop.closed)):
        primitives.extend(
            run_to_primitives(run, k, f"{prefix}_{i}", loop.segment_ids, region_id)
        )
    return primitives
