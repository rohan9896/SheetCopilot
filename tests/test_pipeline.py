"""Tests for SheetCopilot pipeline prototype."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sheetcopilot.chains import chain_segments, fit_circle
from sheetcopilot.models import Point2D, Segment
from sheetcopilot.vector import parse_german_number, extract_radius_mm


def test_parse_german_number():
    assert parse_german_number("552,1") == pytest.approx(552.1)
    assert parse_german_number("583.3") == pytest.approx(583.3)


def test_run_dir_must_be_inside_repo():
    from sheetcopilot.artifacts import repo_root, resolve_run_dir

    pdf = repo_root() / "fixtures/wear_plate/input.pdf"
    in_repo = resolve_run_dir(pdf, Path("runs/test-run"), None)
    assert in_repo.is_relative_to(repo_root())

    with pytest.raises(ValueError, match="inside the repository"):
        resolve_run_dir(pdf, Path("/tmp/outside-repo"), None)


def test_extract_radius():
    assert extract_radius_mm("R3092") == pytest.approx(3092.0)


def test_fit_circle():
    import math

    cx, cy, r = 100.0, 200.0, 50.0
    pts = [
        (cx + r * math.cos(t), cy + r * math.sin(t))
        for t in [i * 0.3 for i in range(20)]
    ]
    result = fit_circle(pts)
    assert result is not None
    fcx, fcy, fr, dev = result
    assert abs(fr - r) < 0.01
    assert dev < 0.01


def test_chain_segments():
    segs = [
        Segment(
            start=Point2D(x=0, y=0),
            end=Point2D(x=1, y=0),
            stroke_width=5.0,
        ),
        Segment(
            start=Point2D(x=1, y=0),
            end=Point2D(x=2, y=0),
            stroke_width=5.0,
        ),
    ]
    chains = chain_segments(segs, tol_pt=0.1)
    assert len(chains) == 1
    assert len(chains[0].points) == 3


def test_generic_heuristic_has_no_hardcoded_holes():
    from sheetcopilot.llm.provider import heuristic_semantic_fallback
    from sheetcopilot.models import CandidatesResult, CandidateContour, ViewsResult, ViewRegion

    candidates = CandidatesResult(
        candidates=[
            CandidateContour(
                id="contour_0",
                chain_ids=[1],
                bbox=(0, 0, 100, 100),
                area_pt2=10000,
                stroke_width=1.4,
                is_closed=True,
                region_id="main_view",
            )
        ],
        selected_id="contour_0",
    )
    views = ViewsResult(
        views=[
            ViewRegion(id=0, label="main_view", bbox=(0, 0, 200, 200), chain_ids=[1]),
            ViewRegion(id=1, label="title_block", bbox=(500, 500, 800, 800), chain_ids=[]),
        ]
    )
    title = "PART-NO. 99-12-345\nMATERIAL S355JR\nSCALE 1:5\nthickness = 12 mm"
    result = heuristic_semantic_fallback(candidates, views, title)

    assert result.provider == "heuristic"
    assert result.model == "generic-offline"
    assert result.holes == []
    assert result.title_block.part_name is None
    assert result.title_block.part_number == "99-12-345"
    assert result.title_block.material == "S355JR"
    assert result.title_block.scale == "1:5"
    assert result.title_block.thickness_mm == pytest.approx(12.0)
    assert result.outer_contour_candidate_id == "contour_0"


def test_auto_requires_llm_key(monkeypatch):
    from sheetcopilot.llm.provider import run_semantic_extraction
    from sheetcopilot.models import CandidatesResult, ViewsResult

    for key in (
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="No LLM API key"):
        run_semantic_extraction(
            Path("missing.png"),
            CandidatesResult(candidates=[]),
            ViewsResult(views=[]),
            title_text="",
            provider="auto",
        )


WEAR_PLATE_PDF = Path("fixtures/wear_plate/input.pdf")
SAMPLE_PDF = Path("docs/sample-drawings/413-013-000600-00-001-IDM-030678118_EN_260802_200426.pdf")


@pytest.mark.skipif(not WEAR_PLATE_PDF.exists(), reason="Wear plate fixture not present")
def test_pipeline_on_wear_plate_fixture(tmp_path: Path):
    from sheetcopilot.artifacts import ensure_run_dir
    from sheetcopilot.pipeline import run_pipeline_stages

    run_dir = ensure_run_dir(tmp_path / "run")
    results = run_pipeline_stages(WEAR_PLATE_PDF, run_dir, provider="heuristic", render=False)
    validation = results["validation"]
    part = results["part"]
    assert validation.manufacturing_ready is True
    bbox = part.bbox_mm
    assert bbox is not None
    assert bbox[2] - bbox[0] == pytest.approx(583.3, abs=2.0)


def test_preview3d_payload_from_part_definition(tmp_path: Path):
    from sheetcopilot.artifacts import ensure_run_dir, read_json, write_json
    from sheetcopilot.models import GeometryPrimitive, PartDefinition, Point2D
    from sheetcopilot.preview3d import build_preview3d_payload, write_preview3d
    from sheetcopilot.report import generate_report

    run_dir = ensure_run_dir(tmp_path / "preview3d-run")
    part = PartDefinition(
        part_number="TEST-001",
        material="S235",
        thickness_mm=25.0,
        outer_contour=[
            GeometryPrimitive(
                id="line0",
                type="line",
                points=[Point2D(x=0, y=0), Point2D(x=100, y=0)],
            ),
            GeometryPrimitive(
                id="line1",
                type="line",
                points=[Point2D(x=100, y=0), Point2D(x=100, y=50)],
            ),
            GeometryPrimitive(
                id="line2",
                type="line",
                points=[Point2D(x=100, y=50), Point2D(x=0, y=50)],
            ),
            GeometryPrimitive(
                id="line3",
                type="line",
                points=[Point2D(x=0, y=50), Point2D(x=0, y=0)],
            ),
        ],
        internal_features=[
            GeometryPrimitive(
                id="hole1",
                type="circle",
                center=Point2D(x=50, y=25),
                radius_mm=9.0,
            )
        ],
        secondary_operations=[{"type": "countersink", "diameter_mm": 34.0}],
    )
    write_json(run_dir, "10_part_definition", part.model_dump())

    out = write_preview3d(part, run_dir)
    assert out.name == "preview3d.json"
    payload = build_preview3d_payload(part)

    assert payload["schema_version"] == "1.0"
    assert payload["thickness_mm"] == pytest.approx(25.0)
    assert len(payload["outer"]) >= 3
    assert payload["holes"][0]["radius_mm"] == pytest.approx(9.0)
    assert payload["secondary_operations"][0]["type"] == "countersink"

    saved = read_json(run_dir, "preview3d")
    assert saved["outer"] == payload["outer"]

    report_path = generate_report(run_dir)
    html = report_path.read_text(encoding="utf-8")
    assert "3D Part Preview" in html
    assert "preview3d-canvas" in html
    assert "three.module.js" in html

    report_no_3d = generate_report(run_dir, include_preview3d=False)
    assert "3D Part Preview" not in report_no_3d.read_text(encoding="utf-8")


@pytest.mark.skipif(not SAMPLE_PDF.exists(), reason="Sample PDF not present")
def test_full_pipeline_with_heuristic(tmp_path: Path):
    from sheetcopilot.artifacts import ensure_run_dir
    from sheetcopilot.pipeline import run_pipeline_stages

    run_dir = ensure_run_dir(tmp_path / "run")
    results = run_pipeline_stages(SAMPLE_PDF, run_dir, provider="heuristic", render=False)
    part = results["part"]
    assert part.outer_contour


@pytest.mark.skipif(
    not SAMPLE_PDF.exists() or not os.environ.get("GROQ_API_KEY"),
    reason="Sample PDF or GROQ_API_KEY not available",
)
def test_llm_semantic_via_groq(tmp_path: Path):
    from dotenv import load_dotenv

    load_dotenv()
    from sheetcopilot.pipeline import run_pipeline_stages

    results = run_pipeline_stages(SAMPLE_PDF, tmp_path / "run", provider="groq", render=True)
    semantic = results["semantic"]
    assert semantic.provider == "groq"
    assert semantic.outer_contour_candidate_id or semantic.primary_contour_id
