"""Prompt templates for semantic vision-LLM pass."""

SEMANTIC_SYSTEM_PROMPT = """You are a manufacturing drawing interpreter for steel fabrication workshops.
You analyze engineering drawing images and return structured JSON only.
You NEVER output coordinates or geometry — only semantic labels and selections from provided candidate lists.
Refer ONLY to deterministic candidate IDs and region IDs supplied by the pipeline.
If uncertain, use null rather than guessing manufacturing-critical values."""

SEMANTIC_USER_PROMPT = """Analyze this engineering drawing image.

Candidate contours (pick ONE as the primary flat part cut profile):
{candidates_json}

Drawing regions detected:
{regions_json}

Title block text snippets from PDF extraction:
{title_text}

Return JSON matching this schema exactly:
{{
  "title_block": {{
    "part_number": "string or null",
    "part_name": "string or null",
    "material": "string or null",
    "thickness_mm": number or null,
    "scale": "string like 1:2.5 or null",
    "units": "mm",
    "revision_date": "string or null"
  }},
  "main_view_region_id": "region id string or null",
  "outer_contour_candidate_id": "candidate id string",
  "cut_hole_candidate_ids": ["candidate id strings for through-hole cut circles"],
  "excluded_contour_ids": ["candidate ids for section views, title block, page border, reference geometry"],
  "secondary_operations": [
    {{
      "candidate_id": "candidate id or null",
      "type": "countersink|counterbore|chamfer|secondary",
      "notes": "optional"
    }}
  ]
}}

Rules:
- The outer contour candidate is the main flat wear plate / cut profile in the primary manufacturing view.
- Exclude section detail views, title block graphics, page borders, and adjacent reference parts.
- Through holes to be cut on CNC are separate from countersinks/counterbores (secondary operations).
- Use only candidate IDs from the list above — never invent IDs or coordinates.
- Page-border candidates (very large, hugging sheet edges) must be excluded.
"""
