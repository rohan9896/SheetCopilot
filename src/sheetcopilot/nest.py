"""Nesting via spyrrow (sparrow) with swappable engine interface."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from shapely.geometry import MultiPolygon, Polygon
from shapely.affinity import rotate, translate

from sheetcopilot.errors import BlockingCode
from sheetcopilot.models import NestingPlacement, NestingResult, PartDefinition

ORTHOGONAL_ORIENTATIONS = [0.0, 90.0, 180.0, 270.0]


class ManufacturingNotReadyError(RuntimeError):
    """Raised when nesting is attempted on an unvalidated part."""

    def __init__(self, reason: str = BlockingCode.NESTING_BLOCKED.value):
        super().__init__(reason)
        self.code = reason


class NestingEngine(ABC):
    @abstractmethod
    def nest(
        self,
        part: PartDefinition,
        quantity: int,
        sheet_width_mm: float,
        sheet_height_mm: float,
        kerf_mm: float,
        clearance_mm: float,
        time_limit_s: float = 30.0,
        manufacturing_ready: bool = True,
    ) -> NestingResult:
        ...


@dataclass
class _PlacedFootprint:
    translation_x: float
    translation_y: float
    rotation_deg: float
    minx: float
    miny: float
    maxx: float
    maxy: float
    sheet_index: int = 0
    x_mm: float = 0.0
    y_mm: float = 0.0


def _arc_points(prim, steps: int | None = None) -> list[tuple[float, float]]:
    """Sample an arc counter-clockwise from its start angle to its end angle."""
    start_deg = prim.start_angle_deg or 0.0
    end_deg = prim.end_angle_deg if prim.end_angle_deg is not None else 360.0
    sa = math.radians(start_deg)
    sweep_deg = (end_deg - start_deg) % 360.0
    if sweep_deg == 0.0 and end_deg != start_deg:
        sweep_deg = 360.0
    sweep = math.radians(sweep_deg)
    if steps is None:
        steps = max(8, int(sweep / (math.pi / 32)))
    return [
        (
            prim.center.x + prim.radius_mm * math.cos(sa + sweep * i / steps),
            prim.center.y + prim.radius_mm * math.sin(sa + sweep * i / steps),
        )
        for i in range(steps + 1)
    ]


def part_to_polygon(part: PartDefinition) -> Polygon | None:
    """
    Build a shapely polygon by walking the outer contour in traversal order.

    Arcs are stored counter-clockwise for DXF, which is not necessarily the
    direction the contour is walked, so each arc is appended in whichever
    direction continues from the previous vertex.
    """
    coords: list[tuple[float, float]] = []

    def append(points: list[tuple[float, float]]) -> None:
        if coords and points:
            head = math.dist(coords[-1], points[0])
            tail = math.dist(coords[-1], points[-1])
            if tail < head:
                points = list(reversed(points))
        for pt in points:
            if not coords or math.dist(coords[-1], pt) > 1e-9:
                coords.append(pt)

    for prim in part.outer_contour:
        if prim.type == "line" and len(prim.points) >= 2:
            append([(p.x, p.y) for p in prim.points])
        elif prim.type == "arc" and prim.center and prim.radius_mm:
            append(_arc_points(prim))
        elif prim.type == "circle" and prim.center and prim.radius_mm:
            append(
                [
                    (
                        prim.center.x + prim.radius_mm * math.cos(2 * math.pi * i / 64),
                        prim.center.y + prim.radius_mm * math.sin(2 * math.pi * i / 64),
                    )
                    for i in range(64)
                ]
            )

    if len(coords) >= 3:
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_empty and poly.area > 0:
            return poly

    # Fallback: convex hull of all outer points
    all_pts: list[tuple[float, float]] = []
    for prim in part.outer_contour:
        for p in prim.points:
            all_pts.append((p.x, p.y))
        if prim.center:
            all_pts.append((prim.center.x, prim.center.y))
    if len(all_pts) >= 3:
        from shapely.geometry import MultiPoint

        hull = MultiPoint(all_pts).convex_hull
        if isinstance(hull, Polygon) and not hull.is_empty:
            return hull

    # Last resort bounding box
    xs, ys = [], []
    for prim in part.outer_contour + part.internal_features:
        if prim.center:
            xs.append(prim.center.x)
            ys.append(prim.center.y)
        for p in prim.points:
            xs.append(p.x)
            ys.append(p.y)
    if len(xs) >= 2:
        return Polygon(
            [
                (min(xs), min(ys)),
                (max(xs), min(ys)),
                (max(xs), max(ys)),
                (min(xs), max(ys)),
            ]
        )
    return None


def polygon_to_spyrrow_item(poly: Polygon) -> list[tuple[float, float]]:
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    coords = list(poly.exterior.coords)
    if coords and coords[0] == coords[-1]:
        coords = coords[:-1]
    minx = min(c[0] for c in coords)
    miny = min(c[1] for c in coords)
    return [(x - minx, y - miny) for x, y in coords]


def normalized_part_polygon(part: PartDefinition) -> Polygon | None:
    """Part polygon normalized to origin, matching spyrrow item coordinates."""
    poly = part_to_polygon(part)
    if poly is None or poly.is_empty:
        return None
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    minx, miny, _, _ = poly.bounds
    return translate(poly, -minx, -miny)


def placement_geometry(
    item_poly: Polygon,
    x_mm: float,
    y_mm: float,
    rotation_deg: float,
) -> Polygon:
    """Reconstruct a placed part footprint on a sheet."""
    placed = rotate(item_poly, rotation_deg, origin=(0, 0), use_radians=False)
    return translate(placed, x_mm, y_mm)


def map_strip_to_sheets(
    footprints: list[_PlacedFootprint],
    sheet_width_mm: float,
    margin_mm: float,
) -> None:
    """
    Slice a strip layout into fixed-width sheets along the strip growth axis (X).

    Uses a greedy band walk so no part straddles a sheet boundary.
    """
    usable_w = sheet_width_mm - 2 * margin_mm
    footprints.sort(key=lambda f: f.minx)
    band_start: float | None = None
    sheet_idx = -1
    for fp in footprints:
        if band_start is None or fp.maxx - band_start > usable_w:
            band_start = fp.minx
            sheet_idx += 1
        fp.sheet_index = sheet_idx
        fp.x_mm = fp.translation_x - band_start + margin_mm
        fp.y_mm = fp.translation_y + margin_mm


def verify_nesting(
    part: PartDefinition,
    nesting: NestingResult,
    *,
    tol_mm: float = 0.05,
) -> list[str]:
    """Return human-readable warnings for an invalid nest layout."""
    warnings: list[str] = []
    item_poly = normalized_part_polygon(part)
    if item_poly is None:
        return ["Could not build part polygon for nesting verification"]

    part_area = item_poly.area
    if nesting.quantity_placed != nesting.quantity_requested:
        warnings.append(
            f"Quantity mismatch: placed {nesting.quantity_placed} of "
            f"{nesting.quantity_requested} requested"
        )

    sheet_geoms: dict[int, list[Polygon]] = {}
    for pl in nesting.placements:
        geom = placement_geometry(item_poly, pl.x_mm, pl.y_mm, pl.rotation_deg)
        b = geom.bounds
        if (
            b[0] < -tol_mm
            or b[1] < -tol_mm
            or b[2] > nesting.sheet_width_mm + tol_mm
            or b[3] > nesting.sheet_height_mm + tol_mm
        ):
            warnings.append(
                f"Placement on sheet {pl.sheet_index + 1} extends outside the sheet "
                f"(bbox x=[{b[0]:.1f},{b[2]:.1f}], y=[{b[1]:.1f},{b[3]:.1f}])"
            )
        sheet_geoms.setdefault(pl.sheet_index, []).append(geom)

    min_sep = nesting.min_separation_mm
    for sheet_idx, geoms in sheet_geoms.items():
        for i in range(len(geoms)):
            for j in range(i + 1, len(geoms)):
                if geoms[i].intersects(geoms[j]):
                    overlap = geoms[i].intersection(geoms[j]).area
                    if overlap > tol_mm:
                        warnings.append(
                            f"Placements overlap on sheet {sheet_idx + 1} "
                            f"(area {overlap:.1f} mm²)"
                        )
                elif geoms[i].distance(geoms[j]) + tol_mm < min_sep:
                    warnings.append(
                        f"Placements on sheet {sheet_idx + 1} are closer than "
                        f"{min_sep:.1f} mm separation"
                    )

    return warnings


def grid_capacity_per_sheet(
    item_w_mm: float,
    item_h_mm: float,
    sheet_width_mm: float,
    sheet_height_mm: float,
    margin_mm: float,
) -> int:
    """
    Parts per sheet from a naive axis-aligned grid, best of both orientations.

    This is the sanity baseline the optimizer must beat: any real packer that
    places fewer than this on a sheet it had enough demand to fill has failed.
    """
    best = 0
    for w, h in ((item_w_mm, item_h_mm), (item_h_mm, item_w_mm)):
        if w <= 0 or h <= 0:
            continue
        usable_w = sheet_width_mm - 2 * margin_mm
        usable_h = sheet_height_mm - 2 * margin_mm
        if usable_w < w or usable_h < h:
            continue
        cols = int((usable_w + margin_mm) // (w + margin_mm))
        rows = int((usable_h + margin_mm) // (h + margin_mm))
        best = max(best, cols * rows)
    return best


def _sheet_utilization(
    part_area: float,
    placements: list[NestingPlacement],
    sheet_count: int,
    sheet_width_mm: float,
    sheet_height_mm: float,
) -> list[float]:
    per_sheet: dict[int, int] = {}
    for pl in placements:
        per_sheet[pl.sheet_index] = per_sheet.get(pl.sheet_index, 0) + 1
    sheet_area = sheet_width_mm * sheet_height_mm
    if sheet_area <= 0 or sheet_count <= 0:
        return []
    return [
        round(per_sheet.get(i, 0) * part_area / sheet_area * 100, 2)
        for i in range(sheet_count)
    ]


class SpyrrowNestingEngine(NestingEngine):
    """
    Strip-pack along sheet height, then slice the strip into fixed-width sheets.

    spyrrow bounds Y at strip_height and grows the layout along X. We therefore
    set strip_height from the sheet height and slice X by the sheet width.
    """

    def nest(
        self,
        part: PartDefinition,
        quantity: int,
        sheet_width_mm: float,
        sheet_height_mm: float,
        kerf_mm: float,
        clearance_mm: float,
        time_limit_s: float = 30.0,
        manufacturing_ready: bool = True,
    ) -> NestingResult:
        if not manufacturing_ready:
            raise ManufacturingNotReadyError(
                "Nesting blocked: part has not passed manufacturing validation"
            )
        margin = kerf_mm + clearance_mm
        poly = part_to_polygon(part)
        warnings: list[str] = []

        if poly is None or poly.is_empty:
            return NestingResult(
                strategy="spyrrow_strip_failed",
                sheet_width_mm=sheet_width_mm,
                sheet_height_mm=sheet_height_mm,
                quantity_requested=quantity,
                quantity_placed=0,
                sheet_count=0,
                utilization_pct=0.0,
                min_separation_mm=margin,
                placements=[],
                warnings=["Could not build part polygon for nesting"],
            )

        item_coords = polygon_to_spyrrow_item(poly)
        item_poly = Polygon(item_coords)
        part_area = poly.area
        item_w = max(c[0] for c in item_coords) - min(c[0] for c in item_coords)
        item_h = max(c[1] for c in item_coords) - min(c[1] for c in item_coords)

        placements: list[NestingPlacement] = []
        quantity_placed = 0
        strip_density: float | None = None

        try:
            import spyrrow

            instance = spyrrow.StripPackingInstance(
                name=part.part_number or "part",
                strip_height=sheet_height_mm - 2 * margin,
                items=[
                    spyrrow.Item(
                        id="part",
                        shape=item_coords,
                        demand=quantity,
                        allowed_orientations=ORTHOGONAL_ORIENTATIONS,
                    )
                ],
            )
            config = spyrrow.StripPackingConfig(
                total_computation_time=int(max(5, time_limit_s)),
                min_items_separation=margin,
                seed=42,
            )
            solution = instance.solve(config)
            strip_density = float(solution.density)

            footprints: list[_PlacedFootprint] = []
            for placed in solution.placed_items:
                rot = float(placed.rotation)
                tx, ty = placed.translation
                geom = translate(rotate(item_poly, rot, origin=(0, 0)), tx, ty)
                b = geom.bounds
                footprints.append(
                    _PlacedFootprint(
                        translation_x=tx,
                        translation_y=ty,
                        rotation_deg=rot,
                        minx=b[0],
                        miny=b[1],
                        maxx=b[2],
                        maxy=b[3],
                    )
                )

            map_strip_to_sheets(footprints, sheet_width_mm, margin)

            for fp in footprints:
                placements.append(
                    NestingPlacement(
                        part_id=part.part_number or "part",
                        x_mm=fp.x_mm,
                        y_mm=fp.y_mm,
                        rotation_deg=fp.rotation_deg,
                        sheet_index=fp.sheet_index,
                    )
                )
                quantity_placed += 1

        except Exception as exc:
            warnings.append(f"spyrrow failed ({exc}); using grid fallback")
            return self._grid_fallback(
                part,
                quantity,
                sheet_width_mm,
                sheet_height_mm,
                margin,
                item_w,
                item_h,
                part_area,
            )

        sheet_count = max((p.sheet_index for p in placements), default=-1) + 1
        total_sheet_area = sheet_count * sheet_width_mm * sheet_height_mm
        used_area = part_area * quantity_placed
        utilization = (used_area / total_sheet_area * 100) if total_sheet_area > 0 else 0.0
        sheet_utils = _sheet_utilization(
            part_area, placements, sheet_count, sheet_width_mm, sheet_height_mm
        )

        baseline = grid_capacity_per_sheet(
            item_w, item_h, sheet_width_mm, sheet_height_mm, margin
        )
        per_sheet_counts: dict[int, int] = {}
        for pl in placements:
            per_sheet_counts[pl.sheet_index] = per_sheet_counts.get(pl.sheet_index, 0) + 1
        max_on_sheet = max(per_sheet_counts.values(), default=0)

        # A sheet that had enough demand to fill it must beat a naive grid; and an
        # order smaller than one sheet's capacity must never spill onto a second.
        underperformed = baseline > 0 and (
            (quantity >= baseline and max_on_sheet < baseline)
            or (quantity < baseline and sheet_count > 1)
        )
        if underperformed:
            warnings.append(
                f"Optimizer placed {max_on_sheet} part(s) on its fullest sheet but a "
                f"naive grid fits {baseline}; falling back to grid nesting"
            )
            fallback = self._grid_fallback(
                part,
                quantity,
                sheet_width_mm,
                sheet_height_mm,
                margin,
                item_w,
                item_h,
                part_area,
            )
            fallback.warnings = warnings + fallback.warnings
            return fallback

        result = NestingResult(
            strategy="spyrrow_strip_then_slice",
            sheet_width_mm=sheet_width_mm,
            sheet_height_mm=sheet_height_mm,
            quantity_requested=quantity,
            quantity_placed=quantity_placed,
            sheet_count=sheet_count,
            utilization_pct=round(utilization, 2),
            min_separation_mm=margin,
            placements=placements,
            warnings=warnings,
            strip_density=round(strip_density, 4) if strip_density is not None else None,
            sheet_utilization_pct=sheet_utils,
            grid_baseline_per_sheet=baseline,
            max_parts_on_a_sheet=max_on_sheet,
            sheet_capacity_utilization_pct=(
                round(max_on_sheet / baseline * 100, 2) if baseline > 0 else None
            ),
        )
        result.warnings.extend(verify_nesting(part, result))
        return result

    def _grid_fallback(
        self,
        part: PartDefinition,
        quantity: int,
        sheet_width_mm: float,
        sheet_height_mm: float,
        min_sep: float,
        item_w: float,
        item_h: float,
        part_area: float,
    ) -> NestingResult:
        """Axis-aligned grid placement, using whichever orientation fits more parts."""
        placements: list[NestingPlacement] = []
        margin = min_sep
        item_poly = normalized_part_polygon(part)

        best: tuple[int, float, int, int, float, float, float, float] | None = None
        for rot in (0.0, 90.0):
            if item_poly is not None:
                b = rotate(item_poly, rot, origin=(0, 0)).bounds
                off_x, off_y = -b[0], -b[1]
                w, h = b[2] - b[0], b[3] - b[1]
            else:
                off_x = off_y = 0.0
                w, h = (item_w, item_h) if rot == 0.0 else (item_h, item_w)
            cols = max(1, int((sheet_width_mm - 2 * margin + margin) // (w + margin)))
            rows = max(1, int((sheet_height_mm - 2 * margin + margin) // (h + margin)))
            if best is None or cols * rows > best[0]:
                best = (cols * rows, rot, cols, rows, w, h, off_x, off_y)

        _, rot, cols, rows, w, h, off_x, off_y = best
        pitch_x, pitch_y = w + margin, h + margin
        sheet_idx = 0
        col = row = 0
        placed = 0

        for _ in range(quantity):
            x = margin + col * pitch_x + off_x
            y = margin + row * pitch_y + off_y
            placements.append(
                NestingPlacement(
                    part_id=part.part_number or "part",
                    x_mm=x,
                    y_mm=y,
                    rotation_deg=rot,
                    sheet_index=sheet_idx,
                )
            )
            placed += 1
            col += 1
            if col >= cols:
                col = 0
                row += 1
            if row >= rows:
                row = 0
                sheet_idx += 1

        # Derive from placements: the row/col cursor wraps past the last part and
        # would otherwise report a trailing empty sheet.
        sheet_count = max((p.sheet_index for p in placements), default=-1) + 1
        utilization = (
            (part_area * placed) / (sheet_count * sheet_width_mm * sheet_height_mm) * 100
            if sheet_count > 0
            else 0.0
        )
        sheet_utils = _sheet_utilization(
            part_area, placements, sheet_count, sheet_width_mm, sheet_height_mm
        )
        baseline = grid_capacity_per_sheet(
            item_w, item_h, sheet_width_mm, sheet_height_mm, min_sep
        )
        per_sheet_counts: dict[int, int] = {}
        for pl in placements:
            per_sheet_counts[pl.sheet_index] = per_sheet_counts.get(pl.sheet_index, 0) + 1
        max_on_sheet = max(per_sheet_counts.values(), default=0)

        result = NestingResult(
            strategy="grid_fallback",
            sheet_width_mm=sheet_width_mm,
            sheet_height_mm=sheet_height_mm,
            quantity_requested=quantity,
            quantity_placed=placed,
            sheet_count=sheet_count,
            utilization_pct=round(utilization, 2),
            min_separation_mm=min_sep,
            placements=placements,
            warnings=["Used grid fallback nesting"],
            sheet_utilization_pct=sheet_utils,
            grid_baseline_per_sheet=baseline,
            max_parts_on_a_sheet=max_on_sheet,
            sheet_capacity_utilization_pct=(
                round(max_on_sheet / baseline * 100, 2) if baseline > 0 else None
            ),
        )
        result.warnings.extend(verify_nesting(part, result))
        return result


def get_nesting_engine(name: str = "spyrrow") -> NestingEngine:
    if name == "spyrrow":
        return SpyrrowNestingEngine()
    raise ValueError(f"Unknown nesting engine: {name}")
