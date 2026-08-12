# SheetCopilot

SheetCopilot converts engineering drawings into reviewable, cut-ready part profiles and automatically nests multiple parts across sheets to reduce CAD effort, material waste, and production-preparation time.

## Pipeline prototype (local CLI)

This repo includes a **drawing-to-DXF pipeline prototype** for validating the core product idea before building the SaaS.

### Setup

```bash
# Requires uv (https://docs.astral.sh/uv/)
uv sync --extra dev
```

### Run on sample drawing

```bash
uv run sheetcopilot --pdf docs/sample-drawings/413-013-000600-00-001-IDM-030678118_EN_260802_200426.pdf run-all
```

Artifacts land in `runs/<slug>/`:

| Stage | File | Description |
|-------|------|-------------|
| 0 | `00_intake.json` | Vector vs raster classification |
| 1 | `01_vector.json` | Segments, stroke widths, positioned text |
| 2 | `02_chains.json` | Chained polylines + arc/circle fits |
| 3 | `03_views.json` | View regions + scale calibration |
| 4 | `04_candidates.json` | Candidate part contours |
| 5 | `05_llm.json` | Semantic extraction (title block, holes) |
| 6 | `06_part_definition.json` | Canonical part geometry |
| 7 | `07_validation.json` | Measured vs annotated dimension gate |
| 8 | `08_nesting.json` | spyrrow/sparrow nesting placements |
| 9 | `09_dxf.json` | DXF export verification |
| — | `report.html` | Single-page inspection report |
| — | `overlay.png` | Reconstruction overlaid on drawing |
| — | `dxf/*.dxf` | Part + nested sheet DXFs |

### Per-stage commands

```bash
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run intake
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run vector
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run chains
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run views
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run llm --provider groq
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run assemble
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run validate-cmd
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run nest --quantity 4
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run dxf
uv run sheetcopilot --pdf <drawing.pdf> --run-dir runs/my-run report
```

Use `--skip-llm` only for offline debugging of geometry/nesting (generic stub — no hole classification). Real semantic extraction requires an LLM key.

### Vision-LLM providers

Default (`auto`) order — **LLM required**:

1. **Groq** — `qwen/qwen3.6-27b` (`GROQ_API_KEY`)
2. **OpenRouter** fallback (`OPENROUTER_API_KEY`, optional `OPENROUTER_MODEL`)

If no key is set, the pipeline errors. Copy `.env.example` to `.env` and fill keys.

`--provider heuristic` is a generic offline stub for debugging only (regex title fields + largest contour; **never invents holes**).

### Tests

```bash
uv run pytest tests/ -v
```

The M1 go/no-go test validates that vector geometry from the sample drawing reproduces annotated radii (R3020–R3190) and linear dimensions (552.1, 583.3, 522.4, 185 mm) with derived scale — not hardcoded.
