"""CLI for resumable pipeline stages."""

from __future__ import annotations

import json
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

from sheetcopilot import __version__
from sheetcopilot.artifacts import (
    artifact_exists,
    ensure_run_dir,
    read_json,
    resolve_run_dir,
    stage_path,
    write_json,
)
from sheetcopilot.dxf import export_nested_dxf, export_part_dxf, verify_dxf_roundtrip
from sheetcopilot.intake import run_intake
from sheetcopilot.nest import ManufacturingNotReadyError, get_nesting_engine
from sheetcopilot.overlay import render_nest_preview, render_overlay
from sheetcopilot.pipeline import run_pipeline_stages
from sheetcopilot.preview3d import write_preview3d
from sheetcopilot.report import generate_report
from sheetcopilot.vector import run_vector_extraction


def _pdf_path(ctx: click.Context) -> Path:
    return ctx.obj["pdf_path"]


def _run_dir(ctx: click.Context) -> Path:
    return ctx.obj["run_dir"]


@click.group()
@click.option("--pdf", "pdf_path", type=click.Path(exists=True, path_type=Path), required=True)
@click.option(
    "--run-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Run directory under the repository (e.g. runs/my-run)",
)
@click.option(
    "--runs-root",
    type=click.Path(path_type=Path),
    default=Path("runs"),
    help="Root folder for auto-created runs (relative to repository root)",
)
@click.pass_context
def cli(ctx: click.Context, pdf_path: Path, run_dir: Path | None, runs_root: Path) -> None:
    """SheetCopilot drawing-to-validated-DXF pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["pdf_path"] = pdf_path.resolve()
    try:
        ctx.obj["run_dir"] = ensure_run_dir(resolve_run_dir(pdf_path, run_dir, runs_root))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Run directory: {ctx.obj['run_dir']}")


@cli.command()
@click.pass_context
def intake(ctx: click.Context) -> None:
    """Stage 0: PDF intake classification."""
    result = run_intake(_pdf_path(ctx))
    write_json(_run_dir(ctx), "00_intake", result.model_dump())
    click.echo(json.dumps(result.model_dump(), indent=2))


@cli.command()
@click.pass_context
def vector(ctx: click.Context) -> None:
    """Stage 1: Vector extraction."""
    result = run_vector_extraction(_pdf_path(ctx))
    write_json(_run_dir(ctx), "01_vector", result.model_dump())
    click.echo(f"Extracted {len(result.segments)} segments, {len(result.texts)} text spans")


@cli.command("preview3d")
@click.pass_context
def preview3d_cmd(ctx: click.Context) -> None:
    """Build optional 3D preview mesh JSON from Part Definition."""
    from sheetcopilot.models import PartDefinition

    run_dir = _run_dir(ctx)
    part = PartDefinition.model_validate(read_json(run_dir, "10_part_definition"))
    out = write_preview3d(part, run_dir)
    click.echo(f"3D preview mesh: {out}")


@cli.command()
@click.option("--no-preview3d", is_flag=True, help="Omit 3D part preview section from report")
@click.pass_context
def report(ctx: click.Context, no_preview3d: bool) -> None:
    """Generate HTML inspection report."""
    out = generate_report(_run_dir(ctx), include_preview3d=not no_preview3d)
    click.echo(f"Report: {out}")


@cli.command()
@click.option("--provider", default="auto", show_default=True)
@click.option("--quantity", default=4, show_default=True)
@click.option("--sheet-width", default=2500.0, show_default=True, type=float)
@click.option("--sheet-height", default=1250.0, show_default=True, type=float)
@click.option("--kerf", default=3.0, show_default=True, type=float)
@click.option("--clearance", default=5.0, show_default=True, type=float)
@click.option(
    "--skip-llm",
    is_flag=True,
    help="DEBUG ONLY: use generic offline heuristic (no hole classification)",
)
@click.option("--no-preview3d", is_flag=True, help="Skip 3D preview mesh and report section")
@click.pass_context
def run_all(
    ctx: click.Context,
    provider: str,
    quantity: int,
    sheet_width: float,
    sheet_height: float,
    kerf: float,
    clearance: float,
    skip_llm: bool,
    no_preview3d: bool,
) -> None:
    """Run full pipeline end-to-end."""
    if skip_llm:
        click.echo("WARNING: --skip-llm uses generic offline heuristic.", err=True)
        provider = "heuristic"
    else:
        import os

        if provider == "auto" and not any(
            os.environ.get(k)
            for k in (
                "GROQ_API_KEY",
                "OPENROUTER_API_KEY",
                "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
                "GEMINI_API_KEY",
            )
        ):
            raise click.ClickException(
                "No LLM API key set. Add GROQ_API_KEY to .env or use --skip-llm."
            )

    run_dir = _run_dir(ctx)
    pdf_path = _pdf_path(ctx)

    results = run_pipeline_stages(pdf_path, run_dir, provider=provider)
    part = results["part"]
    validation = results["validation"]

    click.echo(f"Part: {part.part_number}, outer primitives: {len(part.outer_contour)}")
    click.echo(f"Scale: {part.scale_mm_per_pt:.6f} mm/pt")
    click.echo(
        f"Validation: {'PASS' if validation.passed else 'FAIL'} "
        f"(manufacturing_ready={validation.manufacturing_ready}, status={validation.status})"
    )
    for issue in validation.blocking_issues:
        if issue.severity == "error":
            click.echo(f"  [ERROR] {issue.code}: {issue.message}")

    overlay_path = stage_path(run_dir, "overlay")
    render_overlay(pdf_path, part, overlay_path)
    validation.overlay_path = str(overlay_path)
    write_json(run_dir, "11_validation", validation.model_dump())

    if not no_preview3d and validation.manufacturing_ready:
        write_preview3d(part, run_dir)

    # DXF export only when manufacturing_ready
    from sheetcopilot.models import DxfExportResult, NestingResult

    dxf_result = DxfExportResult(skipped=True, skip_reason="manufacturing_not_ready")
    if validation.manufacturing_ready:
        dxf_dir = run_dir / "dxf"
        dxf_dir.mkdir(exist_ok=True)
        part_path = dxf_dir / f"{(part.part_number or 'part').replace('/', '-')}_rev0.dxf"
        export_part_dxf(part, part_path)
        rt_passed, rt_notes, counts = verify_dxf_roundtrip(part_path, part)
        dxf_result = DxfExportResult(
            part_dxf_path=str(part_path),
            verification_passed=rt_passed,
            roundtrip_passed=rt_passed,
            verification_notes=rt_notes,
            entity_counts=counts,
        )
        click.echo(f"CAM-ready DXF: {part_path} (round-trip: {'PASS' if rt_passed else 'FAIL'})")

        engine = get_nesting_engine("spyrrow")
        try:
            nesting = engine.nest(
                part,
                quantity,
                sheet_width,
                sheet_height,
                kerf,
                clearance,
                manufacturing_ready=validation.manufacturing_ready,
            )
            write_json(run_dir, "13_nesting", nesting.model_dump())
            nested_paths = export_nested_dxf(part, nesting, dxf_dir)
            dxf_result.nested_dxf_paths = [str(p) for p in nested_paths]
            preview_path = stage_path(run_dir, "nest_preview")
            render_nest_preview(part, nesting, preview_path)
            click.echo(
                f"Nested {nesting.quantity_placed}/{nesting.quantity_requested} "
                f"on {nesting.sheet_count} sheet(s) — {nesting.utilization_pct:.1f}% "
                f"sheet area used [{nesting.strategy}]"
            )
            if nesting.grid_baseline_per_sheet:
                click.echo(
                    f"  Capacity: {nesting.max_parts_on_a_sheet} part(s) on the fullest "
                    f"sheet vs grid baseline {nesting.grid_baseline_per_sheet}/sheet. "
                    f"Order at least {nesting.grid_baseline_per_sheet} to fill a sheet."
                )
            for warn in nesting.warnings:
                click.echo(f"  [nesting] {warn}", err=True)
        except ManufacturingNotReadyError as exc:
            click.echo(f"Nesting blocked: {exc}", err=True)
    else:
        click.echo("DXF export and nesting skipped — manufacturing validation did not pass.", err=True)

    write_json(run_dir, "12_dxf", dxf_result.model_dump())
    ctx.invoke(report, no_preview3d=no_preview3d)
    click.echo("Pipeline complete.")


@cli.command()
def version() -> None:
    click.echo(f"sheetcopilot {__version__}")


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
