"""Strict pydantic schemas for vision-LLM semantic extraction."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LLMTitleBlockSchema(BaseModel):
    part_number: str | None = None
    part_name: str | None = None
    material: str | None = None
    thickness_mm: float | None = None
    scale: str | None = None
    units: str = "mm"
    revision_date: str | None = None


class LLMSecondaryOpSchema(BaseModel):
    candidate_id: str | None = None
    type: str = "secondary"
    notes: str | None = None


class LLMSemanticSchema(BaseModel):
    """LLM must return this shape — candidate IDs only, no coordinates."""

    title_block: LLMTitleBlockSchema
    main_view_region_id: str | None = None
    outer_contour_candidate_id: str
    cut_hole_candidate_ids: list[str] = Field(default_factory=list)
    excluded_contour_ids: list[str] = Field(default_factory=list)
    secondary_operations: list[LLMSecondaryOpSchema] = Field(default_factory=list)
    # Legacy compat
    primary_contour_id: int | str | None = None
    primary_view_id: int | str | None = None
    holes: list[dict] = Field(default_factory=list)
