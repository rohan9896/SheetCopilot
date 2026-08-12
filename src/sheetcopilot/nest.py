"""Nesting via spyrrow (sparrow) with swappable engine interface."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

from shapely.geometry import Polygon
from shapely.affinity import rotate, translate

from sheetcopilot.errors import BlockingCode
from sheetcopilot.models import NestingPlacement, NestingResult, PartDefinition


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


def _arc_points(prim, steps: int | None = None) -> list[tuple[float, float]]:
    """Sample an arc counter-clockwise from its start angle to its end angle."""
    import math

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
    import math

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
    from shapely.geometry import MultiPolygon

    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    coords = list(poly.exterior.coords)
    if coords and coords[0] == coords[-1]:
        coords = coords[:-1]
    # Normalize to origin
    minx = min(c[0] for c in coords)
    miny = min(c[1] for c in coords)
    return [(x - minx, y - miny) for x, y in coords]


class SpyrrowNestingEngine(NestingEngine):
    """
    Uses spyrrow strip packing at fixed sheet width, then maps to fixed-height sheets.
    Strategy: strip-pack-then-slice (M4 spike option A).
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
        min_sep = kerf_mm + clearance_mm
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
                min_separation_mm=min_sep,
                placements=[],
                warnings=["Could not build part polygon for nesting"],
            )

        item_coords = polygon_to_spyrrow_item(poly)
        part_area = poly.area
        item_w = max(c[0] for c in item_coords) - min(c[0] for c in item_coords)
        item_h = max(c[1] for c in item_coords) - min(c[1] for c in item_coords)

        placements: list[NestingPlacement] = []
        quantity_placed = 0

        try:
            import spyrrow

            instance = spyrrow.StripPackingInstance(
                name=part.part_number or "part",
                strip_height=sheet_width_mm - 2 * min_sep,
                items=[
                    spyrrow.Item(
                        id="part",
                        shape=item_coords,
                        demand=quantity,
                        allowed_orientations=None,
                    )
                ],
            )
            config = spyrrow.StripPackingConfig(
                total_computation_time=int(max(5, time_limit_s)),
                min_items_separation=min_sep,
                seed=42,
            )
            solution = instance.solve(config)

            strip_length = solution.width
            sheet_count = max(1, math.ceil(strip_length / sheet_height_mm))

            for placed in solution.placed_items:
                x = placed.translation[0] + min_sep
                y = placed.translation[1] + min_sep
                rot = math.degrees(placed.rotation) if abs(placed.rotation) <= 2 * math.pi else placed.rotation
                sheet_idx = int(y // sheet_height_mm) if sheet_height_mm > 0 else 0
                local_y = y % sheet_height_mm if sheet_height_mm > 0 else y
                placements.append(
                    NestingPlacement(
                        part_id=part.part_number or "part",
                        x_mm=x,
                        y_mm=local_y,
                        rotation_deg=float(rot),
                        sheet_index=sheet_idx,
                    )
                )
                quantity_placed += 1

        except Exception as exc:
            warnings.append(f"spyrrow failed ({exc}); using grid fallback")
            return self._grid_fallback(
                part, quantity, sheet_width_mm, sheet_height_mm, min_sep, item_w, item_h, part_area
            )

        # The strip-length estimate can disagree with where items actually landed.
        if placements:
            sheet_count = max(p.sheet_index for p in placements) + 1

        total_sheet_area = sheet_count * sheet_width_mm * sheet_height_mm
        used_area = part_area * quantity_placed
        utilization = (used_area / total_sheet_area * 100) if total_sheet_area > 0 else 0.0

        return NestingResult(
            strategy="spyrrow_strip_then_slice",
            sheet_width_mm=sheet_width_mm,
            sheet_height_mm=sheet_height_mm,
            quantity_requested=quantity,
            quantity_placed=quantity_placed,
            sheet_count=sheet_count,
            utilization_pct=round(utilization, 2),
            min_separation_mm=min_sep,
            placements=placements,
            warnings=warnings,
        )

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
        """Simple grid placement when spyrrow unavailable."""
        placements: list[NestingPlacement] = []
        pitch_x = item_w + min_sep
        pitch_y = item_h + min_sep
        margin = min_sep
        cols = max(1, int((sheet_width_mm - 2 * margin) // pitch_x))
        rows = max(1, int((sheet_height_mm - 2 * margin) // pitch_y))
        per_sheet = cols * rows
        sheet_idx = 0
        col = row = 0
        placed = 0

        for _ in range(quantity):
            x = margin + col * pitch_x
            y = margin + row * pitch_y
            placements.append(
                NestingPlacement(
                    part_id=part.part_number or "part",
                    x_mm=x,
                    y_mm=y,
                    rotation_deg=0.0,
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

        sheet_count = sheet_idx + 1
        utilization = (part_area * placed) / (sheet_count * sheet_width_mm * sheet_height_mm) * 100

        return NestingResult(
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
        )


def get_nesting_engine(name: str = "spyrrow") -> NestingEngine:
    if name == "spyrrow":
        return SpyrrowNestingEngine()
    raise ValueError(f"Unknown nesting engine: {name}")
