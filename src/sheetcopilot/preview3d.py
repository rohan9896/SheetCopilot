"""Optional Three.js extruded-plate preview (removable module).

Builds a lightweight mesh JSON from PartDefinition and an HTML viewer snippet
for the pipeline report. Does not feed manufacturing truth.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from shapely.geometry import MultiPolygon

from sheetcopilot.models import PartDefinition
from sheetcopilot.nest import part_to_polygon

DEFAULT_THICKNESS_MM = 25.0
PREVIEW3D_FILENAME = "preview3d.json"


def build_preview3d_payload(part: PartDefinition) -> dict[str, Any]:
    """PartDefinition → preview3d.json mesh contract."""
    poly = part_to_polygon(part)
    if poly is None or poly.is_empty:
        raise ValueError("Cannot build 3D preview without outer contour geometry")

    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)

    cx, cy = poly.centroid.x, poly.centroid.y
    outer_coords = list(poly.exterior.coords)
    if outer_coords and outer_coords[0] == outer_coords[-1]:
        outer_coords = outer_coords[:-1]
    outer = [[x - cx, y - cy] for x, y in outer_coords]

    holes: list[dict[str, Any]] = []
    for feat in part.internal_features:
        if feat.type == "circle" and feat.center and feat.radius_mm:
            holes.append(
                {
                    "center": [feat.center.x - cx, feat.center.y - cy],
                    "radius_mm": feat.radius_mm,
                }
            )

    thickness = part.thickness_mm if part.thickness_mm is not None else DEFAULT_THICKNESS_MM
    thickness_note = None
    if part.thickness_mm is None:
        thickness_note = f"Thickness not in title block; using {DEFAULT_THICKNESS_MM} mm default"

    xs = [p[0] for p in outer]
    ys = [p[1] for p in outer]
    bbox_mm = [min(xs), min(ys), max(xs), max(ys)] if xs and ys else [0, 0, 0, 0]

    return {
        "schema_version": "1.0",
        "units": part.units or "mm",
        "part_number": part.part_number,
        "part_name": part.part_name,
        "material": part.material,
        "thickness_mm": thickness,
        "thickness_note": thickness_note,
        "outer": outer,
        "holes": holes,
        "secondary_operations": list(part.secondary_operations),
        "bbox_mm": bbox_mm,
    }


def write_preview3d(part: PartDefinition, run_dir: Path) -> Path:
    payload = build_preview3d_payload(part)
    path = run_dir / PREVIEW3D_FILENAME
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_preview3d(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_preview3d_section(data: dict[str, Any]) -> str:
    """HTML + Three.js viewer snippet for report embedding."""
    payload_json = json.dumps(data).replace("</", "<\\/")

    material = data.get("material") or "—"
    thickness = data.get("thickness_mm", DEFAULT_THICKNESS_MM)
    thickness_note = data.get("thickness_note") or ""
    cut_hole_count = len(data.get("holes") or [])
    secondary = data.get("secondary_operations") or []

    secondary_lines = []
    for op in secondary:
        op_type = op.get("type", "secondary")
        dia = op.get("diameter_mm")
        notes = op.get("notes", "")
        line = f"{op_type}"
        if dia is not None:
            line += f" Ø{dia:g} mm"
        if notes:
            line += f" — {notes}"
        secondary_lines.append(line)

    secondary_html = ""
    if secondary_lines:
        items = "".join(f"<li>{html.escape(line)}</li>" for line in secondary_lines)
        secondary_html = (
            f'<div class="preview3d-secondary"><strong>Secondary operations</strong> '
            f"(not modeled in 3D):<ul>{items}</ul></div>"
        )

    note_html = ""
    if thickness_note:
        note_html = f'<p class="preview3d-note">{html.escape(thickness_note)}</p>'

    legend = (
        f"Thickness: {thickness:g} mm · Material: {html.escape(str(material))} · "
        f"Cut holes: {cut_hole_count} (extruded voids) · "
        f"Drag to orbit, scroll to zoom, right-drag to pan"
    )

    return f"""
<h2>3D Part Preview</h2>
<div id="preview3d-wrap" class="preview3d-wrap">
  <canvas id="preview3d-canvas"></canvas>
  <p class="preview3d-legend">{legend}</p>
  {note_html}
  {secondary_html}
</div>
<script type="importmap">
{{
  "imports": {{
    "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
  }}
}}
</script>
<script type="application/json" id="preview3d-data">{payload_json}</script>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';

const data = JSON.parse(document.getElementById('preview3d-data').textContent);
const canvas = document.getElementById('preview3d-canvas');
const wrap = document.getElementById('preview3d-wrap');

const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true }});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setClearColor(0x1a1a2e);

const scene = new THREE.Scene();
scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 0.85);
key.position.set(120, 180, 220);
scene.add(key);
const fill = new THREE.DirectionalLight(0x8899ff, 0.35);
fill.position.set(-100, -80, 100);
scene.add(fill);

const shape = new THREE.Shape();
const outer = data.outer || [];
if (outer.length >= 3) {{
  shape.moveTo(outer[0][0], outer[0][1]);
  for (let i = 1; i < outer.length; i++) shape.lineTo(outer[i][0], outer[i][1]);
  shape.closePath();
}}

for (const hole of (data.holes || [])) {{
  const path = new THREE.Path();
  path.absarc(hole.center[0], hole.center[1], hole.radius_mm, 0, Math.PI * 2, false);
  shape.holes.push(path);
}}

const depth = data.thickness_mm || {DEFAULT_THICKNESS_MM};
const geometry = new THREE.ExtrudeGeometry(shape, {{
  depth,
  bevelEnabled: false,
}});
geometry.computeVertexNormals();

const material = new THREE.MeshStandardMaterial({{
  color: 0x6b8cae,
  metalness: 0.15,
  roughness: 0.65,
  side: THREE.DoubleSide,
}});
const mesh = new THREE.Mesh(geometry, material);
scene.add(mesh);

const edges = new THREE.EdgesGeometry(geometry, 25);
const line = new THREE.LineSegments(
  edges,
  new THREE.LineBasicMaterial({{ color: 0x223344, transparent: true, opacity: 0.55 }})
);
scene.add(line);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

function resize() {{
  const w = wrap.clientWidth;
  const h = 480;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}}

function frameCamera() {{
  const box = new THREE.Box3().setFromObject(mesh);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 1);
  camera.position.set(center.x + maxDim * 1.4, center.y - maxDim * 1.1, center.z + maxDim * 1.6);
  controls.target.copy(center);
  controls.update();
}}

resize();
frameCamera();
window.addEventListener('resize', () => {{ resize(); frameCamera(); }});

(function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}})();
</script>
"""
