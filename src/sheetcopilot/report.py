"""HTML report for pipeline run inspection."""

from __future__ import annotations

import html
import json
from pathlib import Path

from sheetcopilot.artifacts import STAGE_FILES, artifact_exists, read_json
from sheetcopilot.preview3d import load_preview3d, render_preview3d_section


def _pre(data: object) -> str:
    return f"<pre>{html.escape(json.dumps(data, indent=2, default=str))}</pre>"


def generate_report(run_dir: Path, *, include_preview3d: bool = True) -> Path:
    sections: list[str] = []
    title = run_dir.name

    sections.append(f"<h1>SheetCopilot Pipeline Report: {html.escape(title)}</h1>")

    stage_order = [
        ("00_intake", "Intake"),
        ("01_vector", "Vector Extraction"),
        ("02_regions", "Drawing Regions"),
        ("03_classified", "Primitive Classification"),
        ("04_geometry", "Geometry Chains"),
        ("05_candidates", "Contour Candidates"),
        ("06_dimensions", "Dimensions"),
        ("07_scale", "Scale Consensus"),
        ("08_semantic", "LLM Semantic Extraction"),
        ("09_features", "Hole Features"),
        ("10_part_definition", "Part Definition"),
        ("11_validation", "Validation Gate"),
        ("12_dxf", "CAM-ready DXF Export"),
        ("13_nesting", "Nesting"),
    ]

    for stage_key, label in stage_order:
        if artifact_exists(run_dir, stage_key):
            data = read_json(run_dir, stage_key)
            sections.append(f"<h2>{label}</h2>")
            sections.append(_pre(data))

    # Validation badge
    if artifact_exists(run_dir, "11_validation"):
        val = read_json(run_dir, "11_validation")
        ready = val.get("manufacturing_ready", False)
        status = val.get("status", "failed")
        badge = "MANUFACTURING READY" if ready else status.upper()
        color = "green" if ready else "red"
        sections.insert(
            1,
            f'<p style="font-size:1.4em;color:{color}"><strong>{badge}</strong></p>',
        )
        if val.get("blocking_issues"):
            sections.insert(
                2,
                "<h3>Blocking Issues</h3><ul>"
                + "".join(
                    f"<li><strong>{html.escape(i['code'])}</strong>: "
                    f"{html.escape(i['message'])}</li>"
                    for i in val["blocking_issues"]
                    if i.get("severity") == "error"
                )
                + "</ul>",
            )

    # Debug images
    for img_key, label in [
        ("cut_preview", "CUT Geometry Sent To Nesting"),
        ("regions_render", "Drawing Regions"),
        ("eligible_render", "Eligible Geometry"),
        ("candidates_render", "Contour Candidates"),
        ("selected_part_render", "Selected Part"),
        ("overlay", "Validation Overlay"),
        ("nest_preview", "Nest Sheet Preview"),
    ]:
        img_path = run_dir / STAGE_FILES.get(img_key, f"{img_key}.png")
        if img_path.exists():
            sections.append(f"<h2>{label}</h2>")
            sections.append(f'<img src="{img_path.name}" style="max-width:100%" />')

    nest_sheets = sorted(run_dir.glob("nest_sheet*.png"))
    for sheet_img in nest_sheets:
        sections.append(f"<h3>{html.escape(sheet_img.stem)}</h3>")
        sections.append(f'<img src="{sheet_img.name}" style="max-width:100%" />')

    dxf_dir = run_dir / "dxf"
    if dxf_dir.exists():
        sections.append("<h2>DXF Files</h2><ul>")
        for f in sorted(dxf_dir.glob("*.dxf")):
            sections.append(f'<li><a href="dxf/{f.name}">{html.escape(f.name)}</a></li>')
        sections.append("</ul>")

    preview3d_path = run_dir / STAGE_FILES["preview3d"]
    if include_preview3d and preview3d_path.exists():
        sections.append(render_preview3d_section(load_preview3d(preview3d_path)))

    body = "\n".join(sections)
    html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 1200px; }}
pre {{ background: #f4f4f4; padding: 1rem; overflow: auto; font-size: 12px; }}
h2 {{ border-bottom: 1px solid #ccc; padding-bottom: 0.3rem; }}
.preview3d-wrap {{ margin: 1rem 0 2rem; }}
#preview3d-canvas {{ width: 100%; height: 480px; display: block; background: #1a1a2e; border-radius: 4px; }}
</style></head><body>{body}</body></html>"""

    out = run_dir / STAGE_FILES["report"]
    out.write_text(html_content, encoding="utf-8")
    return out
