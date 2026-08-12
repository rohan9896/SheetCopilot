"""Render overlays, candidate labels, and nest sheet previews."""

from __future__ import annotations

import math
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from shapely.affinity import rotate, translate
from shapely.geometry import MultiPolygon, Polygon

from sheetcopilot.models import (
    CandidatesResult,
    ClassifiedResult,
    GeometryPrimitive,
    NestingResult,
    PartDefinition,
    RegionsResult,
)
from sheetcopilot.nest import part_to_polygon


def render_pdf_page(pdf_path: Path, dpi: int = 200) -> Image.Image:
    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[0]
    scale = dpi / 72.0
    bitmap = page.render(scale=scale)
    pil = bitmap.to_pil()
    doc.close()
    return pil.convert("RGBA")


def _page_to_px(img: Image.Image, scale: float):
    """Map y-up page points onto the top-down raster of the same page."""
    page_height = img.height / scale

    def convert(x: float, y: float) -> tuple[float, float]:
        return x * scale, (page_height - y) * scale

    return convert


def _bbox_to_px(bbox: tuple[float, float, float, float], convert) -> list[float]:
    x0, y0 = convert(bbox[0], bbox[3])
    x1, y1 = convert(bbox[2], bbox[1])
    return [x0, y0, x1, y1]


def render_candidates(
    pdf_path: Path,
    candidates: CandidatesResult,
    output_path: Path,
    dpi: int = 150,
) -> Path:
    img = render_pdf_page(pdf_path, dpi=dpi)
    draw = ImageDraw.Draw(img)
    scale = dpi / 72.0
    to_px = _page_to_px(img, scale)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except OSError:
        font = ImageFont.load_default()

    for c in candidates.candidates:
        x0, y0, x1, y1 = c.bbox
        color = (255, 0, 0, 200) if c.is_page_frame else (255, 0, 0, 200)
        if c.id == candidates.selected_id:
            color = (0, 180, 0, 220)
        draw.rectangle(_bbox_to_px((x0, y0, x1, y1), to_px), outline=color, width=3)
        cx, cy = to_px((x0 + x1) / 2, (y0 + y1) / 2)
        label = f"{c.id}\n{c.rank_score:.0f}"
        draw.text((cx, cy), label, fill=color, font=font, anchor="mm")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def render_overlay(
    pdf_path: Path,
    part: PartDefinition,
    output_path: Path,
    dpi: int = 200,
) -> Path:
    """Overlay reconstructed geometry in green on original drawing."""
    img = render_pdf_page(pdf_path, dpi=dpi)
    draw = ImageDraw.Draw(img)
    scale_pt = dpi / 72.0
    mm_per_pt = part.scale_mm_per_pt or ((25.4 / 72.0) * 2.5)
    to_px = _page_to_px(img, scale_pt)

    def mm_to_px(x_mm: float, y_mm: float) -> tuple[float, float]:
        return to_px(x_mm / mm_per_pt, y_mm / mm_per_pt)

    for prim in part.outer_contour + part.internal_features:
        if prim.type == "arc" and prim.center and prim.radius_mm:
            pts = [mm_to_px(x, y) for x, y in _tessellate_arc(prim)]
            draw.line(pts, fill=(0, 200, 0, 220), width=2, joint="curve")
        elif prim.type == "line" and len(prim.points) >= 2:
            p0 = mm_to_px(prim.points[0].x, prim.points[0].y)
            p1 = mm_to_px(prim.points[1].x, prim.points[1].y)
            draw.line([p0, p1], fill=(0, 200, 0, 220), width=2)
        elif prim.type == "circle" and prim.center and prim.radius_mm:
            cx, cy = mm_to_px(prim.center.x, prim.center.y)
            r_px = prim.radius_mm / mm_per_pt * scale_pt
            draw.ellipse(
                [cx - r_px, cy - r_px, cx + r_px, cy + r_px],
                outline=(0, 200, 0, 220),
                width=2,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def render_regions(
    pdf_path: Path,
    regions: RegionsResult,
    output_path: Path,
    dpi: int = 150,
) -> Path:
    """Overlay detected drawing regions."""
    img = render_pdf_page(pdf_path, dpi=dpi)
    draw = ImageDraw.Draw(img)
    scale = dpi / 72.0
    colors = {
        "page_frame": (255, 0, 0),
        "title_block": (255, 165, 0),
        "main_view": (0, 200, 0),
        "section_view": (0, 100, 255),
    }
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
    except OSError:
        font = ImageFont.load_default()

    to_px = _page_to_px(img, scale)
    for region in regions.regions:
        color = colors.get(region.label, (128, 128, 128))
        box = _bbox_to_px(region.bbox, to_px)
        draw.rectangle(box, outline=color, width=2)
        draw.text(
            (box[0] + 4, box[1] + 4),
            f"{region.id}: {region.label}",
            fill=color,
            font=font,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def render_eligible_geometry(
    pdf_path: Path,
    classified: ClassifiedResult,
    output_path: Path,
    dpi: int = 150,
) -> Path:
    """Show only manufacturing-candidate geometry."""
    img = render_pdf_page(pdf_path, dpi=dpi)
    draw = ImageDraw.Draw(img)
    scale = dpi / 72.0
    to_px = _page_to_px(img, scale)
    for seg in classified.segments:
        if seg.category.value != "manufacturing_candidate":
            continue
        draw.line(
            [to_px(seg.start.x, seg.start.y), to_px(seg.end.x, seg.end.y)],
            fill=(0, 200, 0),
            width=2,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def render_selected_part(
    pdf_path: Path,
    part: PartDefinition,
    output_path: Path,
    dpi: int = 200,
) -> Path:
    """Highlight selected CUT geometry."""
    return render_overlay(pdf_path, part, output_path, dpi=dpi)


def _tessellate_arc(prim: GeometryPrimitive, steps: int = 64) -> list[tuple[float, float]]:
    cx, cy = prim.center.x, prim.center.y
    r = prim.radius_mm or 0.0
    start_deg = prim.start_angle_deg or 0.0
    end_deg = prim.end_angle_deg if prim.end_angle_deg is not None else 360.0
    start = math.radians(start_deg)
    sweep_deg = (end_deg - start_deg) % 360.0
    if sweep_deg == 0.0 and end_deg != start_deg:
        sweep_deg = 360.0
    sweep = math.radians(sweep_deg)
    return [
        (cx + r * math.cos(start + sweep * i / steps), cy + r * math.sin(start + sweep * i / steps))
        for i in range(steps + 1)
    ]


def render_cut_preview(
    part: PartDefinition,
    output_path: Path,
    px_per_mm: float = 1.6,
    margin_px: int = 40,
) -> Path:
    """
    Draw only the CUT geometry that will be nested.

    Nothing from the source page is drawn, so any title block, dimension line,
    centre mark or section-view stroke that leaked into the part is immediately
    visible here.
    """
    outer = part.outer_contour
    cut_holes = [
        prim
        for prim in part.internal_features
        if prim.type == "circle" and prim.center and prim.radius_mm
    ]
    if not outer:
        raise ValueError("part has no outer contour to preview")

    bbox = part.bbox_mm or (0.0, 0.0, 1.0, 1.0)
    width_mm = max(bbox[2] - bbox[0], 1e-6)
    height_mm = max(bbox[3] - bbox[1], 1e-6)
    img_w = int(width_mm * px_per_mm) + 2 * margin_px
    img_h = int(height_mm * px_per_mm) + 2 * margin_px + 28

    img = Image.new("RGB", (img_w, img_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    def to_px(x: float, y: float) -> tuple[float, float]:
        return (
            margin_px + (x - bbox[0]) * px_per_mm,
            margin_px + (bbox[3] - y) * px_per_mm,
        )

    for prim in outer:
        if prim.type == "arc" and prim.center:
            pts = [to_px(x, y) for x, y in _tessellate_arc(prim)]
            draw.line(pts, fill=(0, 0, 0), width=2, joint="curve")
        elif prim.type == "circle" and prim.center:
            cx, cy = prim.center.x, prim.center.y
            r = prim.radius_mm or 0.0
            x0, y0 = to_px(cx - r, cy + r)
            x1, y1 = to_px(cx + r, cy - r)
            draw.ellipse([x0, y0, x1, y1], outline=(0, 0, 0), width=2)
        elif len(prim.points) >= 2:
            draw.line(
                [to_px(p.x, p.y) for p in prim.points], fill=(0, 0, 0), width=2, joint="curve"
            )

    for prim in cut_holes:
        cx, cy = prim.center.x, prim.center.y
        r = prim.radius_mm or 0.0
        x0, y0 = to_px(cx - r, cy + r)
        x1, y1 = to_px(cx + r, cy - r)
        draw.ellipse([x0, y0, x1, y1], outline=(180, 0, 0), width=2)

    font = _load_font(13)
    caption = (
        f"CUT geometry only — {width_mm:.1f} x {height_mm:.1f} mm, "
        f"{len(outer)} primitives, {len(cut_holes)} through hole(s)"
    )
    draw.text((margin_px, img_h - 22), caption, fill=(60, 60, 60), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _normalized_part_polygon(part: PartDefinition) -> Polygon | None:
    poly = part_to_polygon(part)
    if poly is None or poly.is_empty:
        return None
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    minx, miny, _, _ = poly.bounds
    return translate(poly, -minx, -miny)


def _draw_sheet(
    nesting: NestingResult,
    part_poly: Polygon,
    sheet_index: int,
    px_per_mm: float,
    font: ImageFont.ImageFont,
    label_font: ImageFont.ImageFont,
) -> Image.Image:
    w_px = max(1, int(nesting.sheet_width_mm * px_per_mm))
    h_px = max(1, int(nesting.sheet_height_mm * px_per_mm))
    margin = 40
    img = Image.new("RGB", (w_px + 2 * margin, h_px + 2 * margin + 36), (245, 245, 245))
    draw = ImageDraw.Draw(img)

    title = (
        f"Sheet {sheet_index + 1}  ·  {nesting.sheet_width_mm:.0f}×{nesting.sheet_height_mm:.0f} mm  ·  "
        f"util {nesting.utilization_pct:.1f}%  ·  sep {nesting.min_separation_mm:.1f} mm"
    )
    draw.text((margin, 8), title, fill=(40, 40, 40), font=label_font)

    ox, oy = margin, margin + 28
    draw.rectangle(
        [ox, oy, ox + w_px, oy + h_px],
        outline=(80, 80, 80),
        fill=(255, 255, 255),
        width=2,
    )

    m = nesting.min_separation_mm * px_per_mm
    if m > 1:
        draw.rectangle(
            [ox + m, oy + m, ox + w_px - m, oy + h_px - m],
            outline=(200, 200, 200),
            width=1,
        )

    colors = [
        (30, 120, 200),
        (200, 80, 40),
        (40, 150, 90),
        (150, 60, 160),
        (180, 140, 20),
        (60, 60, 60),
    ]

    sheet_placements = [p for p in nesting.placements if p.sheet_index == sheet_index]
    for i, pl in enumerate(sheet_placements):
        color = colors[i % len(colors)]
        placed = rotate(part_poly, pl.rotation_deg, origin=(0, 0), use_radians=False)
        placed = translate(placed, pl.x_mm, pl.y_mm)

        geoms = list(placed.geoms) if isinstance(placed, MultiPolygon) else [placed]
        for geom in geoms:
            if geom.is_empty:
                continue
            coords = [
                (ox + x * px_per_mm, oy + (nesting.sheet_height_mm - y) * px_per_mm)
                for x, y in geom.exterior.coords
            ]
            if len(coords) >= 3:
                # Light fill via temporary RGBA layer
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                layer_draw = ImageDraw.Draw(layer)
                layer_draw.polygon(coords, fill=(*color, 55), outline=(*color, 230))
                img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
                draw = ImageDraw.Draw(img)
                draw.line(coords, fill=color, width=2)

        try:
            c = placed.centroid
            cx = ox + c.x * px_per_mm
            cy = oy + (nesting.sheet_height_mm - c.y) * px_per_mm
        except Exception:
            cx = ox + pl.x_mm * px_per_mm
            cy = oy + (nesting.sheet_height_mm - pl.y_mm) * px_per_mm

        draw.text((cx, cy), f"{i + 1}", fill=color, font=font, anchor="mm")
        if abs(pl.rotation_deg) > 0.5:
            draw.text(
                (cx, cy + 14),
                f"{pl.rotation_deg:.0f}°",
                fill=(100, 100, 100),
                font=label_font,
                anchor="mm",
            )

    draw.text(
        (ox, oy + h_px + 8),
        f"{len(sheet_placements)} part(s)  ·  strategy={nesting.strategy}",
        fill=(90, 90, 90),
        font=label_font,
    )
    return img


def render_nest_preview(
    part: PartDefinition,
    nesting: NestingResult,
    output_path: Path,
    px_per_mm: float | None = None,
) -> list[Path]:
    """
    Render one PNG per used sheet, plus a stacked nest_preview.png at output_path.
    Returns all written image paths (combined first).
    """
    part_poly = _normalized_part_polygon(part)
    if part_poly is None:
        return []

    if px_per_mm is None:
        longest = max(nesting.sheet_width_mm, nesting.sheet_height_mm, 1.0)
        px_per_mm = 900.0 / longest

    font = _load_font(20)
    label_font = _load_font(14)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sheet_indices = sorted({p.sheet_index for p in nesting.placements})
    if not sheet_indices:
        sheet_indices = list(range(max(1, nesting.sheet_count)))

    sheet_images: list[Image.Image] = []
    written: list[Path] = []

    for idx in sheet_indices:
        img = _draw_sheet(nesting, part_poly, idx, px_per_mm, font, label_font)
        sheet_path = output_path.parent / f"nest_sheet{idx + 1}.png"
        img.save(sheet_path)
        written.append(sheet_path)
        sheet_images.append(img)

    if not sheet_images:
        return written

    gap = 24
    width = max(im.width for im in sheet_images)
    height = sum(im.height for im in sheet_images) + gap * (len(sheet_images) - 1)
    combined = Image.new("RGB", (width, height), (230, 230, 230))
    y = 0
    for im in sheet_images:
        combined.paste(im, (0, y))
        y += im.height + gap

    combined.save(output_path)
    written.insert(0, output_path)
    return written
