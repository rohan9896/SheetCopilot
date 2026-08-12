"""DXF export and round-trip validation against canonical Part Definition."""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf import units
from shapely.geometry import Polygon

from sheetcopilot.config import TOLERANCES
from sheetcopilot.models import DxfExportResult, NestingResult, PartDefinition
from sheetcopilot.nest import part_to_polygon


def export_part_dxf(part: PartDefinition, output_path: Path) -> Path:
    doc = ezdxf.new("R2010", setup=True)
    doc.units = units.MM
    msp = doc.modelspace()

    for layer, color in [("CUT", 1), ("SECONDARY_OP", 3), ("REFERENCE", 8)]:
        if not doc.layers.has_entry(layer):
            doc.layers.add(layer, color=color)

    for prim in part.outer_contour:
        if prim.type == "line" and len(prim.points) >= 2:
            msp.add_line(
                (prim.points[0].x, prim.points[0].y),
                (prim.points[1].x, prim.points[1].y),
                dxfattribs={"layer": "CUT"},
            )
        elif prim.type == "arc" and prim.center and prim.radius_mm is not None:
            msp.add_arc(
                center=(prim.center.x, prim.center.y),
                radius=prim.radius_mm,
                start_angle=prim.start_angle_deg or 0,
                end_angle=prim.end_angle_deg or 360,
                dxfattribs={"layer": "CUT"},
            )
        elif prim.type == "circle" and prim.center and prim.radius_mm:
            msp.add_circle(
                center=(prim.center.x, prim.center.y),
                radius=prim.radius_mm,
                dxfattribs={"layer": "CUT"},
            )

    for prim in part.internal_features:
        if prim.type == "circle" and prim.center and prim.radius_mm:
            msp.add_circle(
                center=(prim.center.x, prim.center.y),
                radius=prim.radius_mm,
                dxfattribs={"layer": "CUT"},
            )

    for sec in part.secondary_operations:
        dia = sec.get("outer_diameter_mm") or sec.get("diameter_mm")
        hole_id = sec.get("hole_id")
        if dia and hole_id:
            matching = next((h for h in part.holes if h.id == hole_id and h.center), None)
            if matching and matching.center:
                msp.add_circle(
                    center=(matching.center.x, matching.center.y),
                    radius=float(dia) / 2,
                    dxfattribs={"layer": "SECONDARY_OP"},
                )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output_path)
    return output_path


def export_nested_dxf(
    part: PartDefinition,
    nesting: NestingResult,
    output_dir: Path,
    revision: str = "rev0",
) -> list[Path]:
    from shapely.geometry import MultiPolygon
    from shapely.affinity import rotate, translate

    part_num = (part.part_number or "part").replace("/", "-")
    paths: list[Path] = []

    poly = part_to_polygon(part)
    if poly is None:
        return paths

    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)

    minx, miny, _, _ = poly.bounds
    norm_coords = [(x - minx, y - miny) for x, y in poly.exterior.coords]

    sheets: dict[int, list] = {}
    for pl in nesting.placements:
        sheets.setdefault(pl.sheet_index, []).append(pl)

    for sheet_idx, pls in sheets.items():
        doc = ezdxf.new("R2010", setup=True)
        doc.units = units.MM
        msp = doc.modelspace()
        if not doc.layers.has_entry("CUT"):
            doc.layers.add("CUT", color=1)
        if not doc.layers.has_entry("SHEET"):
            doc.layers.add("SHEET", color=8)

        msp.add_lwpolyline(
            [
                (0, 0),
                (nesting.sheet_width_mm, 0),
                (nesting.sheet_width_mm, nesting.sheet_height_mm),
                (0, nesting.sheet_height_mm),
                (0, 0),
            ],
            close=True,
            dxfattribs={"layer": "SHEET"},
        )

        for pl in pls:
            p = Polygon(norm_coords)
            p = rotate(p, pl.rotation_deg, origin=(0, 0))
            p = translate(p, pl.x_mm, pl.y_mm)
            coords = list(p.exterior.coords)
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            msp.add_lwpolyline(coords, close=True, dxfattribs={"layer": "CUT"})

        out = output_dir / f"{part_num}_{revision}_sheet{sheet_idx + 1}.dxf"
        doc.saveas(out)
        paths.append(out)

    # A shorter nest must not leave last run's sheet files behind.
    for stale in output_dir.glob(f"{part_num}_{revision}_sheet*.dxf"):
        if stale not in paths:
            stale.unlink()

    return paths


def _arc_bounds(
    cx: float,
    cy: float,
    r: float,
    start_deg: float,
    end_deg: float,
) -> list[tuple[float, float]]:
    """Extreme points of an arc: endpoints plus any axis crossing it sweeps."""
    sweep = (end_deg - start_deg) % 360.0
    if sweep == 0.0 and end_deg != start_deg:
        sweep = 360.0
    points = [
        (cx + r * math.cos(math.radians(start_deg)), cy + r * math.sin(math.radians(start_deg))),
        (cx + r * math.cos(math.radians(end_deg)), cy + r * math.sin(math.radians(end_deg))),
    ]
    for axis_deg, point in (
        (0.0, (cx + r, cy)),
        (90.0, (cx, cy + r)),
        (180.0, (cx - r, cy)),
        (270.0, (cx, cy - r)),
    ):
        if (axis_deg - start_deg) % 360.0 <= sweep:
            points.append(point)
    return points


def _dxf_bbox(doc: ezdxf.document.Drawing) -> tuple[float, float, float, float] | None:
    msp = doc.modelspace()
    xs: list[float] = []
    ys: list[float] = []
    for entity in msp:
        if entity.dxftype() == "LINE":
            xs.extend([entity.dxf.start.x, entity.dxf.end.x])
            ys.extend([entity.dxf.start.y, entity.dxf.end.y])
        elif entity.dxftype() == "CIRCLE":
            cx, cy = entity.dxf.center.x, entity.dxf.center.y
            r = entity.dxf.radius
            xs.extend([cx - r, cx + r])
            ys.extend([cy - r, cy + r])
        elif entity.dxftype() == "ARC":
            for x, y in _arc_bounds(
                entity.dxf.center.x,
                entity.dxf.center.y,
                entity.dxf.radius,
                entity.dxf.start_angle,
                entity.dxf.end_angle,
            ):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def verify_dxf_roundtrip(
    dxf_path: Path,
    part: PartDefinition,
) -> tuple[bool, list[str], dict[str, int]]:
    """Compare reloaded DXF against canonical Part Definition."""
    notes: list[str] = []
    counts: dict[str, int] = {}

    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as exc:
        return False, [f"Failed to read DXF: {exc}"], counts

    if doc.units != units.MM:
        notes.append(f"Units not MM: {doc.units}")

    msp = doc.modelspace()
    cut_circles: list[tuple[float, float, float]] = []
    cut_arcs = 0
    cut_lines = 0

    for entity in msp:
        et = entity.dxftype()
        counts[et] = counts.get(et, 0) + 1
        layer = entity.dxf.layer
        if layer != "CUT":
            continue
        if et == "CIRCLE":
            cut_circles.append(
                (entity.dxf.center.x, entity.dxf.center.y, entity.dxf.radius * 2)
            )
        elif et == "ARC":
            cut_arcs += 1
        elif et == "LINE":
            cut_lines += 1

    expected_holes = len(part.internal_features) + sum(
        1 for p in part.outer_contour if p.type == "circle"
    )
    if len(cut_circles) < expected_holes and expected_holes > 0:
        notes.append(f"Circle count: DXF={len(cut_circles)} expected>={expected_holes}")

    # Bbox comparison
    dxf_bbox = _dxf_bbox(doc)
    part_bbox = part.bbox_mm
    if dxf_bbox and part_bbox:
        dw = dxf_bbox[2] - dxf_bbox[0]
        pw = part_bbox[2] - part_bbox[0]
        if pw > 0 and abs(dw - pw) / pw > TOLERANCES.dxf_area_tolerance_pct / 100:
            notes.append(f"Bbox width mismatch: DXF={dw:.1f}mm vs part={pw:.1f}mm")

    # Hole diameter comparison
    for prim in part.internal_features:
        if prim.type != "circle" or not prim.center or not prim.radius_mm:
            continue
        expected_dia = prim.radius_mm * 2
        best = min(
            (abs(d - expected_dia) for _, _, d in cut_circles),
            default=float("inf"),
        )
        if best > TOLERANCES.hole_diameter_tolerance_mm:
            notes.append(f"Hole Ø{expected_dia:.1f}mm not found in DXF (best delta={best:.1f})")

    passed = not notes and (cut_lines + cut_arcs + len(cut_circles)) > 0
    return passed, notes, counts


def verify_dxf(dxf_path: Path, expected_area_mm2: float | None = None) -> tuple[bool, list[str], dict[str, int]]:
    """Legacy verify — prefer verify_dxf_roundtrip."""
    notes: list[str] = []
    counts: dict[str, int] = {}
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as exc:
        return False, [f"Failed to read DXF: {exc}"], counts

    msp = doc.modelspace()
    for entity in msp:
        counts[entity.dxftype()] = counts.get(entity.dxftype(), 0) + 1

    cut_entities = sum(1 for e in msp if e.dxf.layer == "CUT")
    if cut_entities == 0:
        notes.append("No entities on CUT layer")

    passed = cut_entities > 0
    return passed, notes, counts
