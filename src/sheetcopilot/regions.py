"""Drawing region detection: page frame, title block, main view, sections."""

from __future__ import annotations

import math
import re

from sheetcopilot.config import TOLERANCES
from sheetcopilot.loops import connected_components, find_profile_loops
from sheetcopilot.models import DrawingRegion, RegionsResult, Segment, TextSpan, VectorExtraction


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_union(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _segment_bbox(seg: Segment) -> tuple[float, float, float, float]:
    return (
        min(seg.start.x, seg.end.x),
        min(seg.start.y, seg.end.y),
        max(seg.start.x, seg.end.x),
        max(seg.start.y, seg.end.y),
    )


def detect_page_frame(
    segments: list[Segment],
    page_width: float,
    page_height: float,
) -> DrawingRegion | None:
    """Detect outer drawing sheet border as a near-page-size rectangle."""
    page_area = page_width * page_height
    tol = TOLERANCES.page_frame_edge_margin_pt
    best: DrawingRegion | None = None
    best_score = 0.0

    # Group thick segments into connected components
    thick = [s for s in segments if s.stroke_width >= TOLERANCES.thick_stroke_pt]
    components = connected_components(thick, tol=0.5)

    for comp_segs in components:
        xs: list[float] = []
        ys: list[float] = []
        for s in comp_segs:
            xs.extend([s.start.x, s.end.x])
            ys.extend([s.start.y, s.end.y])
        if not xs:
            continue
        bbox = (min(xs), min(ys), max(xs), max(ys))
        area = _bbox_area(bbox)
        area_ratio = area / page_area if page_area > 0 else 0

        # Must cover most of page and hug edges
        hugs_edges = (
            bbox[0] <= tol
            and bbox[1] <= tol
            and bbox[2] >= page_width - tol
            and bbox[3] >= page_height - tol
        )
        if area_ratio >= TOLERANCES.page_frame_area_ratio and hugs_edges:
            score = area_ratio
            if score > best_score:
                best_score = score
                best = DrawingRegion(
                    id="page_frame",
                    label="page_frame",
                    bbox=bbox,
                    confidence=min(0.99, score),
                    reason="near-page-size rectangle hugging perimeter",
                )
    return best


def detect_title_block(
    texts: list[TextSpan],
    page_width: float,
    page_height: float,
) -> DrawingRegion:
    """Title block in the bottom-right corner (low y in page-up space)."""
    x0 = page_width * TOLERANCES.title_block_x_frac
    y1 = page_height * (1.0 - TOLERANCES.title_block_y_frac)
    bbox = (x0, 0.0, page_width, y1)

    text_in_block = sum(
        1
        for t in texts
        if (t.x0 + t.x1) / 2 >= x0 and (t.y0 + t.y1) / 2 <= y1
    )
    confidence = min(0.95, 0.5 + text_in_block * 0.02)

    return DrawingRegion(
        id="title_block",
        label="title_block",
        bbox=bbox,
        confidence=confidence,
        reason=f"{text_in_block} text spans in bottom-right corner",
    )


def detect_main_view(
    segments: list[Segment],
    page_frame: DrawingRegion | None,
    title_block: DrawingRegion,
    texts: list[TextSpan],
    page_width: float | None = None,
    page_height: float | None = None,
) -> DrawingRegion | None:
    """
    Main manufacturing view: the envelope of the largest closed part-profile loop.

    Using the profile loop rather than a positional window guarantees the region
    covers the whole part, and keeps dimension and annotation geometry out of it.
    """
    if page_width is None:
        page_width = page_frame.bbox[2] if page_frame else max(
            (max(s.start.x, s.end.x) for s in segments), default=1000.0
        )
    if page_height is None:
        page_height = page_frame.bbox[3] if page_frame else max(
            (max(s.start.y, s.end.y) for s in segments), default=1000.0
        )

    loops = find_profile_loops(segments, page_width, page_height)
    if not loops:
        return None

    profile = loops[0]
    pad = TOLERANCES.main_view_pad_pt
    bbox = (
        max(0.0, profile.bbox[0] - pad),
        max(0.0, profile.bbox[1] - pad),
        min(page_width, profile.bbox[2] + pad),
        min(page_height, profile.bbox[3] + pad),
    )
    return DrawingRegion(
        id="main_view",
        label="main_view",
        bbox=bbox,
        confidence=0.95,
        reason=(
            f"largest closed profile loop ({len(profile.segments)} segments, "
            f"stroke {profile.stroke_width:g}pt)"
        ),
    )


def detect_section_views(
    texts: list[TextSpan],
    segments: list[Segment],
    main_view: DrawingRegion | None,
) -> list[DrawingRegion]:
    """Detect section/detail views by label text."""
    sections: list[DrawingRegion] = []
    section_labels = re.compile(r"A-A|SECTION|Schnitt|DETAIL|DET\.|Ansicht", re.I)

    for span in texts:
        if not section_labels.search(span.text):
            continue
        cx = (span.x0 + span.x1) / 2
        cy = (span.y0 + span.y1) / 2

        # Skip if inside main view (label text for section callout)
        if main_view:
            mv = main_view.bbox
            if mv[0] <= cx <= mv[2] and mv[1] <= cy <= mv[3]:
                continue

        # Find nearby thick geometry cluster
        nearby = [
            s
            for s in segments
            if s.stroke_width >= TOLERANCES.thick_stroke_pt
            and math.hypot((s.start.x + s.end.x) / 2 - cx, (s.start.y + s.end.y) / 2 - cy) < 200
        ]
        if not nearby:
            continue

        xs = [c for s in nearby for c in (s.start.x, s.end.x)]
        ys = [c for s in nearby for c in (s.start.y, s.end.y)]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        sid = f"section_{len(sections)}"
        sections.append(
            DrawingRegion(
                id=sid,
                label="section_view",
                bbox=bbox,
                confidence=0.7,
                reason=f"section label '{span.text}'",
            )
        )
    return sections


def assign_segment_regions(
    segments: list[Segment],
    regions: list[DrawingRegion],
) -> list[Segment]:
    """Assign each segment to its containing region."""
    updated: list[Segment] = []
    for seg in segments:
        cx = (seg.start.x + seg.end.x) / 2
        cy = (seg.start.y + seg.end.y) / 2
        best_region: str | None = None
        best_priority = -1
        priority = {
            "page_frame": 1,
            "title_block": 2,
            "section_view": 3,
            "main_view": 4,
        }
        for region in regions:
            b = region.bbox
            if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
                p = priority.get(region.label, 0)
                if p > best_priority:
                    best_priority = p
                    best_region = region.id
        updated.append(seg.model_copy(update={"region_id": best_region}))
    return updated


def run_regions(vector: VectorExtraction) -> RegionsResult:
    """Detect all drawing regions."""
    page_frame = detect_page_frame(
        vector.segments, vector.page_width_pt, vector.page_height_pt
    )
    title_block = detect_title_block(
        vector.texts, vector.page_width_pt, vector.page_height_pt
    )
    main_view = detect_main_view(
        vector.segments,
        page_frame,
        title_block,
        vector.texts,
        vector.page_width_pt,
        vector.page_height_pt,
    )
    sections = detect_section_views(vector.texts, vector.segments, main_view)

    regions: list[DrawingRegion] = []
    if page_frame:
        regions.append(page_frame)
    regions.append(title_block)
    if main_view:
        regions.append(main_view)
    regions.extend(sections)

    return RegionsResult(
        page_width_pt=vector.page_width_pt,
        page_height_pt=vector.page_height_pt,
        regions=regions,
        main_view_id=main_view.id if main_view else None,
    )


def point_in_region(x: float, y: float, region: DrawingRegion) -> bool:
    b = region.bbox
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def get_region(regions: RegionsResult, region_id: str) -> DrawingRegion | None:
    for r in regions.regions:
        if r.id == region_id:
            return r
    return None
