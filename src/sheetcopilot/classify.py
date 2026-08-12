"""Primitive classification before chaining."""

from __future__ import annotations

import math
import re
from collections import Counter

from sheetcopilot.config import TOLERANCES
from sheetcopilot.models import (
    ClassifiedResult,
    DrawingRegion,
    PrimitiveCategory,
    RegionsResult,
    Segment,
    TextSpan,
    VectorExtraction,
)
from sheetcopilot.loops import PartProfile
from sheetcopilot.regions import get_region


def _segment_length(seg: Segment) -> float:
    return math.hypot(seg.end.x - seg.start.x, seg.end.y - seg.start.y)


def _segment_bbox(seg: Segment) -> tuple[float, float, float, float]:
    return (
        min(seg.start.x, seg.end.x),
        min(seg.start.y, seg.end.y),
        max(seg.start.x, seg.end.x),
        max(seg.start.y, seg.end.y),
    )


def _bbox_size(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _near_text(
    seg: Segment, texts: list[TextSpan], max_dist: float
) -> TextSpan | None:
    cx = (seg.start.x + seg.end.x) / 2
    cy = (seg.start.y + seg.end.y) / 2
    best: TextSpan | None = None
    best_d = max_dist
    for t in texts:
        tx = (t.x0 + t.x1) / 2
        ty = (t.y0 + t.y1) / 2
        d = math.hypot(cx - tx, cy - ty)
        if d < best_d:
            best_d = d
            best = t
    return best


def classify_segment(
    seg: Segment,
    regions: RegionsResult,
    texts: list[TextSpan],
    page_width: float,
    page_height: float,
) -> PrimitiveCategory:
    """Assign a category to a single segment."""
    length = _segment_length(seg)
    bbox = _segment_bbox(seg)
    w, h = _bbox_size(bbox)
    cx = (seg.start.x + seg.end.x) / 2
    cy = (seg.start.y + seg.end.y) / 2

    region_id = seg.region_id
    region = get_region(regions, region_id) if region_id else None

    # Page frame / border
    if region and region.label == "page_frame":
        return PrimitiveCategory.BORDER

    # Title block
    if region and region.label == "title_block":
        return PrimitiveCategory.TITLE_BLOCK

    # Tiny curves = annotation symbols (arrowheads)
    if seg.source_object_type == "curve":
        if max(w, h) <= TOLERANCES.arrowhead_max_size_pt:
            return PrimitiveCategory.ANNOTATION_SYMBOL

    # Very short thick segments in main view near dimension text = likely arrowhead fragment
    if length < 8.0 and seg.stroke_width >= TOLERANCES.thick_stroke_pt:
        near = _near_text(seg, texts, 30.0)
        if near and re.search(r"\d", near.text):
            return PrimitiveCategory.ANNOTATION_SYMBOL

    # Thin lines near dimension text
    if seg.stroke_width <= TOLERANCES.thin_stroke_pt:
        near = _near_text(seg, texts, TOLERANCES.dimension_text_proximity_pt)
        if near:
            if re.search(r"R\s*\d", near.text, re.I):
                return PrimitiveCategory.DIMENSION_LINE
            if re.search(r"\d+(?:[.,]\d+)?", near.text):
                return PrimitiveCategory.DIMENSION_LINE
        # Very thin short lines
        if length < 30:
            return PrimitiveCategory.CONSTRUCTION

    # Section view geometry — not manufacturing
    if region and region.label == "section_view":
        return PrimitiveCategory.CONSTRUCTION

    # Dashed lines (when available)
    if seg.dash and seg.dash not in ("None", "[]", ""):
        return PrimitiveCategory.CENTERLINE

    # Thick geometry in main view = manufacturing candidate
    if region and region.label == "main_view":
        if seg.stroke_width >= TOLERANCES.thick_stroke_pt:
            return PrimitiveCategory.MANUFACTURING_CANDIDATE

    # Thick geometry outside excluded regions
    if seg.stroke_width >= TOLERANCES.thick_stroke_pt and length > 20:
        if region is None or region.label not in ("page_frame", "title_block", "section_view"):
            return PrimitiveCategory.MANUFACTURING_CANDIDATE

    return PrimitiveCategory.UNKNOWN


def run_classify(
    vector: VectorExtraction,
    regions: RegionsResult,
    profile: PartProfile | None = None,
) -> ClassifiedResult:
    """
    Classify all segments.

    When the part profile is known, membership of it decides what counts as
    manufacturing geometry. Everything else inside the main view is annotation:
    dimension lines, extension lines, centre marks and hatching are all drawn at
    a stroke width the profile does not use, so they are separated by the
    drawing's own line-width convention rather than by a fixed threshold.
    """
    from sheetcopilot.regions import assign_segment_regions

    assigned = assign_segment_regions(vector.segments, regions.regions)
    classified: list[Segment] = []
    counts: Counter[str] = Counter()

    profile_ids: set[str] = set()
    profile_width: float | None = None
    if profile is not None:
        profile_width = profile.stroke_width
        profile_ids = {s.id for s in profile.outer.segments if s.id}
        for circle in profile.circles:
            profile_ids.update(circle.segment_ids)

    for seg in assigned:
        if profile is not None and seg.id in profile_ids:
            cat = PrimitiveCategory.MANUFACTURING_CANDIDATE
        else:
            cat = classify_segment(
                seg, regions, vector.texts, vector.page_width_pt, vector.page_height_pt
            )
            if (
                profile_width is not None
                and cat == PrimitiveCategory.MANUFACTURING_CANDIDATE
                and abs(seg.stroke_width - profile_width)
                > TOLERANCES.stroke_width_match_tol_pt
            ):
                cat = PrimitiveCategory.CONSTRUCTION
        updated = seg.model_copy(update={"category": cat})
        classified.append(updated)
        counts[cat.value] += 1

    return ClassifiedResult(
        segments=classified,
        category_counts=dict(counts),
    )
