"""Run directory and numbered artifact conventions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGE_FILES = {
    "00_intake": "00_intake.json",
    "01_vector": "01_vector.json",
    "02_regions": "02_regions.json",
    "03_classified": "03_classified.json",
    "04_geometry": "04_geometry.json",
    "05_candidates": "05_candidates.json",
    "06_dimensions": "06_dimensions.json",
    "07_scale": "07_scale.json",
    "08_semantic": "08_semantic.json",
    "09_features": "09_features.json",
    "10_part_definition": "10_part_definition.json",
    "11_validation": "11_validation.json",
    "12_dxf": "12_dxf.json",
    "13_nesting": "13_nesting.json",
    "report": "report.html",
    "overlay": "validation_overlay.png",
    "candidates_render": "contour_candidates.png",
    "regions_render": "regions.png",
    "eligible_render": "eligible_geometry.png",
    "selected_part_render": "selected_part.png",
    "cut_preview": "cut_preview.png",
    "nest_preview": "nest_preview.png",
    "preview3d": "preview3d.json",
}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip())
    return slug.strip("-").lower() or "run"


def repo_root() -> Path:
    """Repository root (directory containing pyproject.toml)."""
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd().resolve()


def _resolve_under_repo(path: Path) -> Path:
    """Resolve path relative to repo root; require it stays inside the repo."""
    root = repo_root()
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Run directory must be inside the repository ({root}). Got: {resolved}"
        ) from exc
    return resolved


def default_run_dir(pdf_path: Path, runs_root: Path | None = None) -> Path:
    root = runs_root or (repo_root() / "runs")
    if not root.is_absolute():
        root = repo_root() / root
    stem = slugify(pdf_path.stem)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return root / f"{stem}-{ts}"


def resolve_run_dir(pdf_path: Path, run_dir: Path | None, runs_root: Path | None = None) -> Path:
    root = repo_root()
    runs = runs_root or Path("runs")
    if not runs.is_absolute():
        runs = root / runs

    if run_dir is not None:
        return _resolve_under_repo(run_dir)
    return default_run_dir(pdf_path, runs)


def ensure_run_dir(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def stage_path(run_dir: Path, stage: str) -> Path:
    if stage not in STAGE_FILES:
        raise KeyError(f"Unknown stage: {stage}")
    return run_dir / STAGE_FILES[stage]


def write_json(run_dir: Path, stage: str, data: Any) -> Path:
    path = stage_path(run_dir, stage)
    path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")
    return path


def read_json(run_dir: Path, stage: str) -> Any:
    path = stage_path(run_dir, stage)
    if not path.exists():
        raise FileNotFoundError(f"Missing artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_exists(run_dir: Path, stage: str) -> bool:
    return stage_path(run_dir, stage).exists()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
