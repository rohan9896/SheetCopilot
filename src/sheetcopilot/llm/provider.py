"""Provider-swappable vision-LLM adapters.

Default order (auto):
  1. Groq — qwen/qwen3.6-27b (vision)
  2. OpenRouter — configurable model (Gemini / Anthropic / etc.)

Semantic extraction requires an LLM. A generic offline heuristic exists only
for explicit --provider heuristic (debugging), never as a silent auto fallback.
"""

from __future__ import annotations

import base64
import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from sheetcopilot.llm.prompts import SEMANTIC_SYSTEM_PROMPT, SEMANTIC_USER_PROMPT
from sheetcopilot.llm.schemas import LLMSemanticSchema
from sheetcopilot.models import (
    CandidatesResult,
    Confidence,
    HoleClassification,
    LLMSemanticResult,
    TitleBlockExtraction,
    ViewsResult,
)

# Defaults
GROQ_DEFAULT_MODEL = "qwen/qwen3.6-27b"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_DEFAULT_MODEL = "google/gemini-2.5-flash"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class VisionProvider(ABC):
    @abstractmethod
    def analyze(
        self,
        image_path: Path,
        candidates: CandidatesResult,
        views: ViewsResult,
        title_text: str,
    ) -> LLMSemanticResult:
        ...


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Qwen thinking models may prepend <think>...</think>
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return json.loads(text)


def _schema_to_result(provider: str, model: str, data: dict) -> LLMSemanticResult:
    parsed = LLMSemanticSchema.model_validate(data)
    tb = parsed.title_block
    holes = [
        HoleClassification(
            diameter_mm=h.get("diameter_mm", 0),
            operation="cut" if h.get("operation", "cut").lower() == "cut" else "secondary",
            secondary_type=h.get("secondary_type"),
            notes=h.get("notes"),
            confidence=Confidence.MEDIUM,
        )
        for h in parsed.holes
    ]
    secondary_ops = [
        {"type": s.type, "candidate_id": s.candidate_id, "notes": s.notes}
        for s in parsed.secondary_operations
    ]
    return LLMSemanticResult(
        provider=provider,
        model=model,
        title_block=TitleBlockExtraction(
            part_number=tb.part_number,
            part_name=tb.part_name,
            material=tb.material,
            thickness_mm=tb.thickness_mm,
            scale=tb.scale,
            units=tb.units,
            revision_date=tb.revision_date,
            confidence=Confidence.MEDIUM,
        ),
        main_view_region_id=parsed.main_view_region_id,
        outer_contour_candidate_id=parsed.outer_contour_candidate_id,
        cut_hole_candidate_ids=parsed.cut_hole_candidate_ids,
        excluded_contour_ids=parsed.excluded_contour_ids,
        secondary_operations=secondary_ops,
        primary_contour_id=parsed.primary_contour_id or parsed.outer_contour_candidate_id,
        primary_view_id=parsed.primary_view_id or parsed.main_view_region_id,
        holes=holes,
        raw_response=data,
    )


def _build_prompt(candidates: CandidatesResult, views, title_text: str) -> str:
    regions_json = json.dumps(
        [v.model_dump() for v in views.views[:30]],
        indent=2,
    )
    return SEMANTIC_USER_PROMPT.format(
        candidates_json=json.dumps(
            [c.model_dump() for c in candidates.candidates[:25]], indent=2
        ),
        regions_json=regions_json,
        title_text=title_text[:4000],
    )


def _encode_vision_image(image_path: Path, max_side: int = 1568) -> tuple[str, str]:
    """
    Resize and encode image for vision APIs.
    Returns (media_type, base64_payload).
    """
    from io import BytesIO

    from PIL import Image

    with Image.open(image_path) as img:
        img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > max_side:
            scale = max_side / longest
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()


class OpenAICompatibleVisionProvider(VisionProvider):
    """Shared OpenAI-style chat.completions vision client (Groq, OpenRouter, OpenAI)."""

    provider_name: str = "openai_compatible"
    api_url: str = "https://api.openai.com/v1/chat/completions"
    default_model: str = "gpt-4o"
    api_key_env: str = "OPENAI_API_KEY"
    model_env: str = "OPENAI_MODEL"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get(self.model_env, self.default_model)
        self.api_key = api_key or os.environ.get(self.api_key_env)

    def _extra_headers(self) -> dict[str, str]:
        return {}

    def _extra_payload(self) -> dict:
        return {"response_format": {"type": "json_object"}}

    def analyze(
        self,
        image_path: Path,
        candidates: CandidatesResult,
        views: ViewsResult,
        title_text: str,
    ) -> LLMSemanticResult:
        if not self.api_key:
            raise RuntimeError(f"{self.api_key_env} not set")

        media_type, image_b64 = _encode_vision_image(image_path)
        prompt = _build_prompt(candidates, views, title_text)
        # JSON-mode providers require the word "json" in the message content.
        prompt = prompt + "\n\nRespond with a single JSON object only."

        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SEMANTIC_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                        },
                    ],
                },
            ],
            "temperature": 0.1,
            "max_completion_tokens": 4096,
        }
        payload.update(self._extra_payload())

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self._extra_headers(),
        }

        with httpx.Client(timeout=180.0) as client:
            resp = client.post(self.api_url, headers=headers, json=payload)
            if resp.status_code >= 400:
                detail = resp.text[:800]
                raise RuntimeError(
                    f"{self.provider_name} API error {resp.status_code}: {detail}"
                )
            body = resp.json()

        message = body["choices"][0]["message"]
        text = message.get("content") or ""
        # Some reasoning models put final answer after reasoning fields
        if not text and message.get("reasoning"):
            text = message["reasoning"]
        data = _parse_json_response(text)
        return _schema_to_result(self.provider_name, self.model, data)


class GroqProvider(OpenAICompatibleVisionProvider):
    """Groq vision — default model qwen/qwen3.6-27b."""

    provider_name = "groq"
    api_url = GROQ_API_URL
    default_model = GROQ_DEFAULT_MODEL
    api_key_env = "GROQ_API_KEY"
    model_env = "GROQ_MODEL"

    def _extra_payload(self) -> dict:
        return {
            "response_format": {"type": "json_object"},
            # Prefer non-thinking path when the API supports it
            "reasoning_effort": "none",
        }


class OpenRouterProvider(OpenAICompatibleVisionProvider):
    """OpenRouter fallback — set OPENROUTER_MODEL to gemini/anthropic/etc."""

    provider_name = "openrouter"
    api_url = OPENROUTER_API_URL
    default_model = OPENROUTER_DEFAULT_MODEL
    api_key_env = "OPENROUTER_API_KEY"
    model_env = "OPENROUTER_MODEL"

    def _extra_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        referer = os.environ.get("OPENROUTER_SITE_URL", "https://github.com/sheetcopilot")
        title = os.environ.get("OPENROUTER_APP_NAME", "SheetCopilot")
        headers["HTTP-Referer"] = referer
        headers["X-Title"] = title
        return headers


class AnthropicProvider(VisionProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    def analyze(
        self,
        image_path: Path,
        candidates: CandidatesResult,
        views: ViewsResult,
        title_text: str,
    ) -> LLMSemanticResult:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode()
        prompt = _build_prompt(candidates, views, title_text)

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": SEMANTIC_SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            body = resp.json()

        text = body["content"][0]["text"]
        data = _parse_json_response(text)
        return _schema_to_result("anthropic", self.model, data)


class OpenAIProvider(OpenAICompatibleVisionProvider):
    provider_name = "openai"
    api_url = "https://api.openai.com/v1/chat/completions"
    default_model = "gpt-4o"
    api_key_env = "OPENAI_API_KEY"
    model_env = "OPENAI_MODEL"


class GeminiProvider(VisionProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")

    def analyze(
        self,
        image_path: Path,
        candidates: CandidatesResult,
        views: ViewsResult,
        title_text: str,
    ) -> LLMSemanticResult:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode()
        prompt = _build_prompt(candidates, views, title_text)

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": SEMANTIC_SYSTEM_PROMPT + "\n\n" + prompt},
                        {"inline_data": {"mime_type": "image/png", "data": image_b64}},
                    ]
                }
            ],
            "generationConfig": {"responseMimeType": "application/json"},
        }

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, params={"key": self.api_key}, json=payload)
            resp.raise_for_status()
            body = resp.json()

        text = body["candidates"][0]["content"]["parts"][0]["text"]
        data = _parse_json_response(text)
        return _schema_to_result("gemini", self.model, data)


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _generic_part_number(title_text: str) -> str | None:
    """Best-effort part number from free text — no drawing-specific IDs."""
    # Prefer prefixed forms: EZ 413-13-600
    m = re.search(r"\b([A-Z]{1,4}\s+\d{2,4}[-./]\d{1,4}[-./]\d{2,6})\b", title_text)
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip())
    m = re.search(r"\b(\d{2,4}[-./]\d{1,4}[-./]\d{2,6})\b", title_text)
    if m:
        return m.group(1)
    return None


def _generic_material(title_text: str) -> str | None:
    # Common EN steel grades, e.g. S235J2G3, S355JR
    m = re.search(r"\b(S\d{3}[A-Z0-9]{0,8})\b", title_text)
    return m.group(1) if m else None


def _generic_scale(title_text: str) -> str | None:
    m = re.search(r"\b(\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?)\b", title_text)
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1))


def _generic_thickness_mm(title_text: str) -> float | None:
    """Thickness only when explicitly labelled — never invent a value."""
    patterns = [
        r"(?:thickness|dicke|blechdicke)\s*[=:]?\s*(\d+(?:[.,]\d+)?)\s*(?:mm)?",
        r"\b[sS]\s*[=:]\s*(\d+(?:[.,]\d+)?)\s*(?:mm)?\b",
        r"(\d+(?:[.,]\d+)?)\s*mm\s*(?:thick|dicke)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, title_text, re.IGNORECASE):
            raw = m.group(1).replace(",", ".")
            try:
                val = float(raw)
            except ValueError:
                continue
            if 0.5 <= val <= 200:
                return val
    return None


def heuristic_semantic_fallback(
    candidates: CandidatesResult,
    views: ViewsResult,
    title_text: str,
) -> LLMSemanticResult:
    """
    Generic offline semantic stub for debugging only.

    - Contour: largest non-title-block candidate
    - Title fields: regex over extracted PDF text (no drawing-specific hardcodes)
    - Holes / secondary ops: left empty (unknown) — LLM required for classification
    """
    part_number = _generic_part_number(title_text)
    material = _generic_material(title_text)
    scale = _generic_scale(title_text)
    thickness = _generic_thickness_mm(title_text)

    primary = candidates.selected_id
    excluded: list[str] = []
    non_frame = [c for c in candidates.candidates if not c.is_page_frame]
    if non_frame and primary is None:
        primary = max(non_frame, key=lambda c: c.rank_score).id
    if primary:
        excluded = [c.id for c in non_frame if c.id != primary]

    main_view_id = None
    for v in views.views:
        if v.label in ("main_view", "main"):
            main_view_id = str(v.id) if isinstance(v.id, int) else v.id
            break

    confidence = Confidence.LOW
    if part_number and material:
        confidence = Confidence.MEDIUM

    return LLMSemanticResult(
        provider="heuristic",
        model="generic-offline",
        title_block=TitleBlockExtraction(
            part_number=part_number,
            part_name=None,
            material=material,
            thickness_mm=thickness,
            scale=scale,
            units="mm",
            revision_date=None,
            confidence=confidence,
        ),
        main_view_region_id=main_view_id,
        outer_contour_candidate_id=primary or "",
        cut_hole_candidate_ids=[],
        excluded_contour_ids=excluded,
        secondary_operations=[],
        primary_contour_id=primary,
        primary_view_id=main_view_id,
        holes=[],
        raw_response={
            "source": "heuristic_generic",
            "note": "Offline stub — use Groq/OpenRouter for semantic extraction",
        },
    )


def get_provider(name: str) -> VisionProvider:
    name = name.lower()
    providers: dict[str, type[VisionProvider]] = {
        "groq": GroqProvider,
        "openrouter": OpenRouterProvider,
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "gemini": GeminiProvider,
    }
    if name not in providers:
        raise ValueError(
            f"Unknown provider: {name}. "
            "Use groq|openrouter|anthropic|openai|gemini|heuristic|auto."
        )
    return providers[name]()


def _resolve_auto_provider() -> str | None:
    """Prefer Groq, then OpenRouter, then direct providers."""
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def run_semantic_extraction(
    image_path: Path,
    candidates: CandidatesResult,
    views: ViewsResult,
    title_text: str,
    provider: str = "auto",
) -> LLMSemanticResult:
    if provider == "heuristic":
        return heuristic_semantic_fallback(candidates, views, title_text)

    if provider == "auto":
        resolved = _resolve_auto_provider()
        if resolved is None:
            raise RuntimeError(
                "No LLM API key configured. Set GROQ_API_KEY (preferred) or "
                "OPENROUTER_API_KEY. Semantic extraction requires an LLM — "
                "the offline heuristic is only available via --provider heuristic."
            )
        provider = resolved

    # Groq primary with OpenRouter fallback on failure
    if provider == "groq":
        try:
            return GroqProvider().analyze(image_path, candidates, views, title_text)
        except Exception as groq_err:
            if os.environ.get("OPENROUTER_API_KEY"):
                try:
                    result = OpenRouterProvider().analyze(
                        image_path, candidates, views, title_text
                    )
                    if result.raw_response is None:
                        result.raw_response = {}
                    result.raw_response["_fallback_from"] = f"groq_error: {groq_err}"
                    return result
                except Exception:
                    raise RuntimeError(
                        f"Groq failed ({groq_err}); OpenRouter fallback also failed"
                    ) from groq_err
            raise

    return get_provider(provider).analyze(image_path, candidates, views, title_text)
