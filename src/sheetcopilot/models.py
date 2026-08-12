"""Shared data models for pipeline stages."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Point2D(BaseModel):
    x: float
    y: float


class PrimitiveCategory(str, Enum):
    MANUFACTURING_CANDIDATE = "manufacturing_candidate"
    DIMENSION_LINE = "dimension_line"
    EXTENSION_LINE = "extension_line"
    LEADER = "leader"
    CENTERLINE = "centerline"
    CENTER_MARK = "center_mark"
    CONSTRUCTION = "construction"
    HATCHING = "hatching"
    BORDER = "border"
    TITLE_BLOCK = "title_block"
    SECTION_ARROW = "section_arrow"
    ANNOTATION_SYMBOL = "annotation_symbol"
    UNKNOWN = "unknown"


class Segment(BaseModel):
    id: str = ""
    start: Point2D
    end: Point2D
    stroke_width: float
    page: int = 0
    dash: str | None = None
    stroking_color: str | None = None
    source_object_type: str = "line"
    category: PrimitiveCategory = PrimitiveCategory.UNKNOWN
    region_id: str | None = None


class TextSpan(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 0


class IntakeResult(BaseModel):
    pdf_path: str
    page_count: int
    is_vector: bool
    has_text: bool
    page_width_pt: float
    page_height_pt: float
    notes: list[str] = Field(default_factory=list)


class VectorExtraction(BaseModel):
    segments: list[Segment]
    texts: list[TextSpan]
    page_width_pt: float
    page_height_pt: float


class DrawingRegion(BaseModel):
    id: str
    label: str
    bbox: tuple[float, float, float, float]
    confidence: float = 0.0
    reason: str | None = None


class RegionsResult(BaseModel):
    page_width_pt: float
    page_height_pt: float
    regions: list[DrawingRegion]
    main_view_id: str | None = None


class ClassifiedResult(BaseModel):
    segments: list[Segment]
    category_counts: dict[str, int] = Field(default_factory=dict)


class ChainPoint(BaseModel):
    x: float
    y: float


class Chain(BaseModel):
    id: int
    points: list[ChainPoint]
    stroke_width: float
    closed: bool = False
    length_pt: float = 0.0
    segment_ids: list[str] = Field(default_factory=list)
    region_id: str | None = None


class FittedArc(BaseModel):
    center: Point2D
    radius_pt: float
    max_deviation_pt: float
    start_angle_deg: float | None = None
    end_angle_deg: float | None = None
    confidence: float = 0.0
    source_segment_ids: list[str] = Field(default_factory=list)


class FittedCircle(BaseModel):
    center: Point2D
    radius_pt: float
    max_deviation_pt: float
    confidence: float = 0.0
    source_segment_ids: list[str] = Field(default_factory=list)


class ChainAnalysis(BaseModel):
    chain: Chain
    is_circular: bool = False
    circularity_ratio: float = 0.0
    fitted_arc: FittedArc | None = None
    fitted_circle: FittedCircle | None = None
    bbox: tuple[float, float, float, float] | None = None


class ChainsResult(BaseModel):
    chains: list[ChainAnalysis]
    page_width_pt: float
    page_height_pt: float


class ViewRegion(BaseModel):
    """Legacy shim — prefer DrawingRegion."""

    id: int
    label: str | None = None
    bbox: tuple[float, float, float, float]
    scale_hint: str | None = None
    scale_mm_per_pt: float | None = None
    chain_ids: list[int] = Field(default_factory=list)


class ViewsResult(BaseModel):
    views: list[ViewRegion]
    annotated_radii_mm: list[float] = Field(default_factory=list)
    annotated_linear_mm: list[float] = Field(default_factory=list)
    scale_votes: dict[str, float] = Field(default_factory=dict)


class CandidateContour(BaseModel):
    id: str
    chain_ids: list[int]
    bbox: tuple[float, float, float, float]
    area_pt2: float
    stroke_width: float
    is_closed: bool
    region_id: str | None = None
    perimeter_pt: float = 0.0
    line_count: int = 0
    arc_count: int = 0
    circle_count: int = 0
    page_area_pct: float = 0.0
    distance_from_page_edge_pt: float = 0.0
    is_page_frame: bool = False
    rank_score: float = 0.0
    rank_reason: str | None = None


class CandidatesResult(BaseModel):
    candidates: list[CandidateContour]
    render_path: str | None = None
    selected_id: str | None = None


class DimensionType(str, Enum):
    LINEAR = "linear"
    RADIUS = "radius"
    DIAMETER = "diameter"
    ANGLE = "angle"
    OVERALL_WIDTH = "overall_width"
    OVERALL_HEIGHT = "overall_height"
    HOLE_DIAMETER = "hole_diameter"


class DimensionCandidate(BaseModel):
    id: str
    value_mm: float
    unit: str = "mm"
    dim_type: DimensionType
    text: str
    text_bbox: tuple[float, float, float, float]
    referenced_candidate_ids: list[str] = Field(default_factory=list)
    association_confidence: float = 0.0


class DimensionsResult(BaseModel):
    dimensions: list[DimensionCandidate]
    annotated_linear_mm: list[float] = Field(default_factory=list)
    annotated_radii_mm: list[float] = Field(default_factory=list)
    annotated_diameters_mm: list[float] = Field(default_factory=list)


class ScaleAnchor(BaseModel):
    annotation_mm: float
    measured_pt: float
    scale_mm_per_pt: float
    error_percent: float
    status: Literal["accepted", "outlier", "rejected"]
    dim_type: str
    dimension_id: str | None = None
    source: str = ""


class ScaleResult(BaseModel):
    scale_mm_per_pt: float
    anchors: list[ScaleAnchor]
    consensus_confidence: float = 0.0
    stable: bool = True
    prior_mm_per_pt: float | None = None


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ConfidenceBundle(BaseModel):
    geometry_extraction: float = 0.0
    topology_chaining: float = 0.0
    dimension_association: float = 0.0
    semantic_classification: float = 0.0
    validation: float = 0.0


class TitleBlockExtraction(BaseModel):
    part_number: str | None = None
    part_name: str | None = None
    material: str | None = None
    thickness_mm: float | None = None
    scale: str | None = None
    units: str = "mm"
    revision_date: str | None = None
    confidence: Confidence = Confidence.UNKNOWN


class HoleClassification(BaseModel):
    id: str | None = None
    center: Point2D | None = None
    diameter_mm: float
    operation: Literal["cut", "secondary"]
    secondary_type: str | None = None
    notes: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    candidate_ids: list[str] = Field(default_factory=list)


class LLMSemanticResult(BaseModel):
    provider: str
    model: str
    title_block: TitleBlockExtraction
    main_view_region_id: str | None = None
    outer_contour_candidate_id: str | None = None
    cut_hole_candidate_ids: list[str] = Field(default_factory=list)
    excluded_contour_ids: list[str] = Field(default_factory=list)
    secondary_operations: list[dict] = Field(default_factory=list)
    # Legacy fields
    primary_contour_id: int | str | None = None
    primary_view_id: int | str | None = None
    holes: list[HoleClassification] = Field(default_factory=list)
    raw_response: dict | None = None


class GeometryPrimitive(BaseModel):
    id: str
    type: Literal["line", "arc", "circle"]
    points: list[Point2D] = Field(default_factory=list)
    center: Point2D | None = None
    radius_mm: float | None = None
    start_angle_deg: float | None = None
    end_angle_deg: float | None = None
    source_pdf_object_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    region_id: str | None = None
    geometry_confidence: float = 0.0
    semantic_confidence: float = 0.0


class HoleFeature(BaseModel):
    id: str
    center: Point2D
    through_diameter_mm: float | None = None
    candidate_geometry_ids: list[str] = Field(default_factory=list)
    cut_circle: GeometryPrimitive | None = None
    secondary_ops: list[dict] = Field(default_factory=list)


class FeaturesResult(BaseModel):
    hole_features: list[HoleFeature]
    secondary_operations: list[dict] = Field(default_factory=list)


class PartDefinition(BaseModel):
    schema_version: str = "2.0"
    units: str = "mm"
    part_number: str | None = None
    part_name: str | None = None
    material: str | None = None
    thickness_mm: float | None = None
    scale: str | None = None
    outer_contour: list[GeometryPrimitive] = Field(default_factory=list)
    internal_features: list[GeometryPrimitive] = Field(default_factory=list)
    holes: list[HoleClassification] = Field(default_factory=list)
    secondary_operations: list[dict] = Field(default_factory=list)
    source_contour_id: str | int | None = None
    source_region_id: str | None = None
    scale_mm_per_pt: float | None = None
    confidence: ConfidenceBundle = Field(default_factory=ConfidenceBundle)
    bbox_mm: tuple[float, float, float, float] | None = None


class DimensionCheck(BaseModel):
    label: str
    annotated_mm: float
    measured_mm: float | None
    delta_mm: float | None
    tolerance_mm: float
    passed: bool
    source: str


class BlockingIssue(BaseModel):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    candidate_id: str | None = None
    suggested_candidates: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    passed: bool
    manufacturing_ready: bool = False
    status: Literal["ready", "needs_review", "failed"] = "failed"
    dimension_checks: list[DimensionCheck]
    blocking_issues: list[BlockingIssue]
    warnings: list[BlockingIssue] = Field(default_factory=list)
    validation_summary: dict = Field(default_factory=dict)
    overlay_path: str | None = None
    cut_preview_path: str | None = None


class NestingPlacement(BaseModel):
    part_id: str
    x_mm: float
    y_mm: float
    rotation_deg: float
    sheet_index: int


class NestingResult(BaseModel):
    strategy: str
    sheet_width_mm: float
    sheet_height_mm: float
    quantity_requested: int
    quantity_placed: int
    sheet_count: int
    utilization_pct: float
    min_separation_mm: float
    placements: list[NestingPlacement]
    warnings: list[str] = Field(default_factory=list)
    strip_density: float | None = None
    sheet_utilization_pct: list[float] = Field(default_factory=list)
    grid_baseline_per_sheet: int | None = None
    max_parts_on_a_sheet: int | None = None
    sheet_capacity_utilization_pct: float | None = None


class DxfExportResult(BaseModel):
    part_dxf_path: str | None = None
    nested_dxf_paths: list[str] = Field(default_factory=list)
    verification_passed: bool = False
    verification_notes: list[str] = Field(default_factory=list)
    entity_counts: dict[str, int] = Field(default_factory=dict)
    area_mm2: float | None = None
    roundtrip_passed: bool = False
    skipped: bool = False
    skip_reason: str | None = None
