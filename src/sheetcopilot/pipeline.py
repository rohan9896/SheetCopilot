"""Pipeline orchestrator — runs all stages in order."""

from __future__ import annotations

from pathlib import Path

from sheetcopilot.artifacts import write_json
from sheetcopilot.chains import run_chains_from_classified
from sheetcopilot.classify import run_classify
from sheetcopilot.candidates import run_candidates
from sheetcopilot.dimensions import run_dimensions
from sheetcopilot.features import run_features
from sheetcopilot.loops import extract_part_profile
from sheetcopilot.intake import run_intake
from sheetcopilot.llm.provider import run_semantic_extraction
from sheetcopilot.models import (
    CandidatesResult,
    ChainsResult,
    ClassifiedResult,
    DimensionsResult,
    FeaturesResult,
    LLMSemanticResult,
    PartDefinition,
    RegionsResult,
    ScaleResult,
    ValidationResult,
    VectorExtraction,
)
from sheetcopilot.overlay import (
    render_candidates,
    render_cut_preview,
    render_eligible_geometry,
    render_regions,
    render_selected_part,
)
from sheetcopilot.reconstruct import assemble_part_definition
from sheetcopilot.regions import run_regions
from sheetcopilot.scale import run_scale
from sheetcopilot.validate import run_validation
from sheetcopilot.vector import run_vector_extraction


def run_pipeline_stages(
    pdf_path: Path,
    run_dir: Path,
    provider: str = "heuristic",
    render: bool = True,
) -> dict:
    """Run full pipeline and write all stage artifacts."""
    intake = run_intake(pdf_path)
    write_json(run_dir, "00_intake", intake.model_dump())

    vector = run_vector_extraction(pdf_path)
    write_json(run_dir, "01_vector", vector.model_dump())

    regions = run_regions(vector)
    write_json(run_dir, "02_regions", regions.model_dump())

    profile = extract_part_profile(
        vector.segments, vector.page_width_pt, vector.page_height_pt
    )

    classified = run_classify(vector, regions, profile=profile)
    write_json(run_dir, "03_classified", classified.model_dump())

    chains = run_chains_from_classified(classified, vector.page_width_pt, vector.page_height_pt)
    write_json(run_dir, "04_geometry", chains.model_dump())

    # Initial scale estimate for dimension association
    dims_pre = run_dimensions(vector.texts, classified.segments, chains)
    scale = run_scale(dims_pre, classified.segments, chains, regions, vector.texts)
    write_json(run_dir, "07_scale", scale.model_dump())

    # Re-associate dimensions with scale
    dimensions = run_dimensions(vector.texts, classified.segments, chains, scale.scale_mm_per_pt)
    write_json(run_dir, "06_dimensions", dimensions.model_dump())

    candidates = run_candidates(
        chains,
        regions,
        classified.segments,
        scale.scale_mm_per_pt,
        annotated_widths=[d for d in dimensions.annotated_linear_mm if d > 100],
        profile=profile,
    )
    write_json(run_dir, "05_candidates", candidates.model_dump())

    if render:
        render_regions(pdf_path, regions, run_dir / "regions.png")
        render_eligible_geometry(pdf_path, classified, run_dir / "eligible_geometry.png")
        cand_img = run_dir / "contour_candidates.png"
        render_candidates(pdf_path, candidates, cand_img)
        candidates.render_path = str(cand_img)

    title_text = "\n".join(t.text for t in vector.texts if t.text.strip())
    semantic = run_semantic_extraction(
        Path(candidates.render_path) if candidates.render_path else pdf_path,
        candidates,
        _regions_to_views(regions),
        title_text,
        provider=provider,
    )
    write_json(run_dir, "08_semantic", semantic.model_dump())

    features = run_features(
        chains,
        regions,
        scale,
        dimensions.annotated_diameters_mm,
        segments=classified.segments,
        profile=profile,
    )
    write_json(run_dir, "09_features", features.model_dump())

    part = assemble_part_definition(
        chains, semantic, scale, candidates, features,
        main_view_bbox=next(
            (r.bbox for r in regions.regions if r.id == regions.main_view_id), None
        ),
        profile=profile,
    )
    write_json(run_dir, "10_part_definition", part.model_dump())

    if render:
        render_selected_part(pdf_path, part, run_dir / "selected_part.png")

    validation = run_validation(part, candidates, dimensions, scale)
    if render and part.outer_contour:
        cut_img = run_dir / "cut_preview.png"
        render_cut_preview(part, cut_img)
        validation.cut_preview_path = str(cut_img)
    write_json(run_dir, "11_validation", validation.model_dump())

    return {
        "intake": intake,
        "vector": vector,
        "regions": regions,
        "classified": classified,
        "chains": chains,
        "candidates": candidates,
        "dimensions": dimensions,
        "scale": scale,
        "semantic": semantic,
        "features": features,
        "part": part,
        "validation": validation,
    }


def _regions_to_views(regions: RegionsResult):
    """Adapt RegionsResult to legacy ViewsResult for LLM provider."""
    from sheetcopilot.models import ViewRegion, ViewsResult

    views = [
        ViewRegion(
            id=i,
            label=r.label,
            bbox=r.bbox,
        )
        for i, r in enumerate(regions.regions)
    ]
    return ViewsResult(views=views)
