"""Deterministic manufacturing validation gate."""

from __future__ import annotations

from sheetcopilot.config import TOLERANCES
from sheetcopilot.errors import BlockingCode
from sheetcopilot.models import (
    BlockingIssue,
    CandidateContour,
    CandidatesResult,
    DimensionCheck,
    DimensionsResult,
    PartDefinition,
    ScaleResult,
    ValidationResult,
)
from sheetcopilot.nest import part_to_polygon


def _part_bbox_mm(part: PartDefinition) -> tuple[float, float, float, float] | None:
    if part.bbox_mm:
        return part.bbox_mm
    xs: list[float] = []
    ys: list[float] = []
    for prim in part.outer_contour:
        for p in prim.points:
            xs.append(p.x)
            ys.append(p.y)
        if prim.center and prim.radius_mm:
            xs.extend([prim.center.x - prim.radius_mm, prim.center.x + prim.radius_mm])
            ys.extend([prim.center.y - prim.radius_mm, prim.center.y + prim.radius_mm])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def check_page_frame_selected(
    part: PartDefinition,
    candidates: CandidatesResult,
) -> BlockingIssue | None:
    cid = part.source_contour_id
    if not cid:
        return None
    for c in candidates.candidates:
        if c.id == cid and c.is_page_frame:
            return BlockingIssue(
                code=BlockingCode.DRAWING_FRAME_SELECTED_AS_PART.value,
                message=f"Selected contour {cid} is the page drawing frame",
                candidate_id=cid,
            )
    return None


def check_bbox_vs_annotations(
    part: PartDefinition,
    dimensions: DimensionsResult,
) -> tuple[list[DimensionCheck], list[BlockingIssue]]:
    checks: list[DimensionCheck] = []
    blocking: list[BlockingIssue] = []
    bbox = _part_bbox_mm(part)
    if not bbox:
        return checks, blocking

    width_mm = bbox[2] - bbox[0]
    height_mm = bbox[3] - bbox[1]

    # An annotation describes an overall dimension only if it is already close to
    # the measured value; anything further away belongs to another feature.
    window = TOLERANCES.overall_dim_search_frac
    verified = 0

    for label, measured in (("width", width_mm), ("height", height_mm)):
        near = [
            a
            for a in dimensions.annotated_linear_mm
            if a > 0 and abs(measured - a) / a <= window
        ]
        if not near:
            continue

        ann = min(near, key=lambda a: abs(measured - a))
        delta = abs(measured - ann)
        tol = max(TOLERANCES.bbox_tolerance_mm, ann * 0.03)
        err_pct = delta / ann * 100
        passed = delta <= tol
        verified += 1
        checks.append(
            DimensionCheck(
                label=f"bbox_{label}_vs_{ann}",
                annotated_mm=ann,
                measured_mm=round(measured, 3),
                delta_mm=round(delta, 3),
                tolerance_mm=tol,
                passed=passed,
                source="part_bbox",
            )
        )
        if not passed:
            blocking.append(
                BlockingIssue(
                    code=BlockingCode.BBOX_DIMENSION_MISMATCH.value,
                    message=(
                        f"Part bbox {label}={measured:.1f}mm differs from "
                        f"annotated {ann}mm by {err_pct:.1f}%"
                    ),
                )
            )

    # Geometry that matches no annotated overall dimension is unverifiable, which
    # is how a sheet border or a section view reaches this point.
    if verified == 0 and dimensions.annotated_linear_mm:
        blocking.append(
            BlockingIssue(
                code=BlockingCode.BBOX_DIMENSION_MISMATCH.value,
                message=(
                    f"Neither overall dimension ({width_mm:.1f}mm x {height_mm:.1f}mm) "
                    f"matches any annotated dimension within {window:.0%}"
                ),
            )
        )

    return checks, blocking


def check_hole_diameters(
    part: PartDefinition,
    dimensions: DimensionsResult,
) -> tuple[list[DimensionCheck], list[BlockingIssue]]:
    checks: list[DimensionCheck] = []
    blocking: list[BlockingIssue] = []

    annotated = dimensions.annotated_diameters_mm or []
    annotated = [d for d in annotated if 10 <= d <= 50]
    cut_holes = [h for h in part.holes if h.operation == "cut"]

    for ann_d in annotated:
        best_delta = float("inf")
        best_measured: float | None = None
        for h in cut_holes:
            delta = abs(h.diameter_mm - ann_d)
            if delta < best_delta:
                best_delta = delta
                best_measured = h.diameter_mm

        # Ignore spurious annotations (e.g. chamfer "40") with no nearby cut hole
        if best_measured is None or best_delta / ann_d > 0.35:
            continue

        tol = max(TOLERANCES.hole_diameter_tolerance_mm, ann_d * 0.03)
        passed = best_delta <= tol
        checks.append(
            DimensionCheck(
                label=f"hole_diameter_{ann_d}",
                annotated_mm=ann_d,
                measured_mm=best_measured,
                delta_mm=round(best_delta, 3) if best_measured else None,
                tolerance_mm=tol,
                passed=passed,
                source="hole_features",
            )
        )
        if not passed and best_measured is not None:
            blocking.append(
                BlockingIssue(
                    code=BlockingCode.HOLE_DIAMETER_MISMATCH.value,
                    message=(
                        f"Closest hole Ø{best_measured:.1f}mm does not match "
                        f"annotated Ø{ann_d}mm (delta={best_delta:.1f}mm)"
                    ),
                )
            )
        elif not passed and not cut_holes:
            blocking.append(
                BlockingIssue(
                    code=BlockingCode.HOLE_DIAMETER_MISMATCH.value,
                    message=f"No cut holes found matching annotated Ø{ann_d}mm",
                )
            )

    return checks, blocking


def check_contour_topology(part: PartDefinition) -> list[BlockingIssue]:
    blocking: list[BlockingIssue] = []
    if not part.outer_contour:
        blocking.append(
            BlockingIssue(
                code=BlockingCode.MISSING_CONTOUR.value,
                message="No outer contour assembled",
            )
        )
        return blocking

    poly = part_to_polygon(part)
    if poly is None or poly.is_empty:
        blocking.append(
            BlockingIssue(
                code=BlockingCode.OPEN_OUTER_CONTOUR.value,
                message="Outer contour could not form a valid polygon",
            )
        )
    elif not poly.is_valid:
        blocking.append(
            BlockingIssue(
                code=BlockingCode.SELF_INTERSECTION.value,
                message="Outer contour polygon is not valid (possible self-intersection)",
            )
        )
    return blocking


def check_scale(scale: ScaleResult) -> list[BlockingIssue]:
    if not scale.stable:
        return [
            BlockingIssue(
                code=BlockingCode.UNRESOLVED_SCALE.value,
                message="Scale consensus is not stable — insufficient agreeing anchors",
            )
        ]
    return []


def run_validation(
    part: PartDefinition,
    candidates: CandidatesResult,
    dimensions: DimensionsResult,
    scale: ScaleResult,
) -> ValidationResult:
    """Full deterministic manufacturing validation."""
    blocking: list[BlockingIssue] = []
    warnings: list[BlockingIssue] = []
    dimension_checks: list[DimensionCheck] = []

    if not part.part_number:
        warnings.append(
            BlockingIssue(code=BlockingCode.MISSING_PART_NUMBER.value, message="Part number unknown", severity="warning")
        )
    if not part.material:
        warnings.append(
            BlockingIssue(code=BlockingCode.MISSING_MATERIAL.value, message="Material unknown", severity="warning")
        )
    if part.thickness_mm is None:
        warnings.append(
            BlockingIssue(code=BlockingCode.MISSING_THICKNESS.value, message="Thickness unknown", severity="warning")
        )

    frame_issue = check_page_frame_selected(part, candidates)
    if frame_issue:
        blocking.append(frame_issue)

    bbox_checks, bbox_blocking = check_bbox_vs_annotations(part, dimensions)
    dimension_checks.extend(bbox_checks)
    blocking.extend(bbox_blocking)

    hole_checks, hole_blocking = check_hole_diameters(part, dimensions)
    dimension_checks.extend(hole_checks)
    blocking.extend(hole_blocking)

    blocking.extend(check_contour_topology(part))
    blocking.extend(check_scale(scale))

    errors = [b for b in blocking if b.severity == "error"]
    warnings.extend(b for b in blocking if b.severity == "warning")
    failed_checks = [c for c in dimension_checks if not c.passed]
    manufacturing_ready = (
        len(errors) == 0 and not failed_checks and bool(part.outer_contour)
    )

    if manufacturing_ready:
        status = "ready"
    elif any(b.code == BlockingCode.AMBIGUOUS_MAIN_CONTOUR.value for b in errors):
        status = "needs_review"
    else:
        status = "failed"

    bbox = _part_bbox_mm(part)
    summary = {
        "bbox_mm": list(bbox) if bbox else None,
        "bbox_width_mm": round(bbox[2] - bbox[0], 2) if bbox else None,
        "bbox_height_mm": round(bbox[3] - bbox[1], 2) if bbox else None,
        "hole_count": len([h for h in part.holes if h.operation == "cut"]),
        "scale_mm_per_pt": part.scale_mm_per_pt,
        "scale_stable": scale.stable,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "failed_dimension_checks": [c.label for c in failed_checks],
    }

    return ValidationResult(
        passed=manufacturing_ready,
        manufacturing_ready=manufacturing_ready,
        status=status,
        dimension_checks=dimension_checks,
        blocking_issues=errors,
        warnings=warnings,
        validation_summary=summary,
    )
