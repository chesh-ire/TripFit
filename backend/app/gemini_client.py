"""Google Gemini provider — the free-tier alternative to Claude.

Implements the same three AI tasks as ``claude_client`` with an identical
interface (``parse_itinerary`` / ``recognize_clothing`` / ``generate_outfits``)
so ``main`` can swap providers transparently. Calls the Gemini REST API with
``httpx`` (already a dependency), using structured output
(``responseMimeType=application/json`` + ``responseSchema``) so responses are
machine-parseable and validated against the same Pydantic models.

The prompts and JSON schemas are reused verbatim from ``claude_client`` — only
the schema *dialect* differs (Gemini uses an OpenAPI-style Schema), so we
translate on the way out.
"""
from __future__ import annotations

import base64
import json

import httpx

# Reuse the prompt engineering + schemas so the two providers stay in lockstep.
from .claude_client import (
    IMAGE_MEDIA_TYPES,
    _ITINERARY_SCHEMA,
    _ITINERARY_SYSTEM,
    _OUTFITS_SCHEMA,
    _OUTFITS_SYSTEM,
    _RECOGNIZE_SCHEMA,
    _RECOGNIZE_SYSTEM,
)
from .config import get_settings
from .schemas import (
    GenerateOutfitsRequest,
    GenerateOutfitsResponse,
    ParsedItinerary,
    RecognizeResponse,
)

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "object": "OBJECT",
    "array": "ARRAY",
}


def _to_gemini_schema(node: dict) -> dict:
    """Translate our JSON Schema into Gemini's OpenAPI-style Schema subset.

    Handles the constructs our schemas actually use: nullable unions
    (``["string", "null"]`` → ``nullable``), enums, nested objects/arrays, and
    required lists. Drops keys Gemini's Schema doesn't accept
    (``additionalProperties``, numeric bounds — those are re-checked by Pydantic).
    """
    out: dict = {}
    t = node.get("type")
    nullable = False
    if isinstance(t, list):
        nullable = "null" in t
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if non_null else "string"
    if t is not None:
        out["type"] = _TYPE_MAP.get(t, "STRING")
    if nullable:
        out["nullable"] = True
    if "description" in node:
        out["description"] = node["description"]
    if "enum" in node:
        out["enum"] = node["enum"]
    if "properties" in node:
        out["properties"] = {k: _to_gemini_schema(v) for k, v in node["properties"].items()}
        # Stabilise field order for more reliable structured output.
        out["propertyOrdering"] = list(node["properties"].keys())
    if "required" in node:
        out["required"] = node["required"]
    if "items" in node:
        out["items"] = _to_gemini_schema(node["items"])
    return out


def _inline(media_type: str, b64: str) -> dict:
    return {"inline_data": {"mime_type": media_type, "data": b64}}


async def _generate(parts: list[dict], schema: dict, system: str) -> dict:
    """Run one structured-output request and return the parsed JSON object."""
    s = get_settings()
    url = f"{_API_BASE}/models/{s.gemini_model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _to_gemini_schema(schema),
            "temperature": 0,
        },
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, params={"key": s.gemini_api_key}, json=body)

    if resp.status_code != 200:
        # Surface Gemini's own error message (quota, bad key, etc.) concisely.
        detail = resp.text[:300]
        raise RuntimeError(f"Gemini API error {resp.status_code}: {detail}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "unknown")
        raise RuntimeError(f"Gemini returned no candidates (blocked: {reason}).")
    finish = candidates[0].get("finishReason")
    if finish in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
        raise RuntimeError(f"Gemini declined to process this request ({finish}).")
    parts_out = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts_out)
    if not text.strip():
        raise RuntimeError("Empty response from Gemini.")
    return json.loads(text)


# ---------------------------------------------------------------------------
# 1. Itinerary parsing
# ---------------------------------------------------------------------------
async def parse_itinerary(file_bytes: bytes, content_type: str, filename: str) -> ParsedItinerary:
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        block = _inline("application/pdf", b64)
    else:
        media = content_type if content_type in IMAGE_MEDIA_TYPES else "image/jpeg"
        block = _inline(media, b64)
    parts = [block, {"text": "Extract the travel itinerary from this file."}]
    data = await _generate(parts, _ITINERARY_SCHEMA, _ITINERARY_SYSTEM)
    return ParsedItinerary.model_validate(data)


# ---------------------------------------------------------------------------
# 2. Clothing recognition
# ---------------------------------------------------------------------------
async def recognize_clothing(file_bytes: bytes, content_type: str) -> RecognizeResponse:
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    media = content_type if content_type in IMAGE_MEDIA_TYPES else "image/jpeg"
    parts = [
        _inline(media, b64),
        {"text": "Identify and categorize the clothing in this photo."},
    ]
    data = await _generate(parts, _RECOGNIZE_SCHEMA, _RECOGNIZE_SYSTEM)
    return RecognizeResponse.model_validate(data)


# ---------------------------------------------------------------------------
# 3. Outfit generation
# ---------------------------------------------------------------------------
async def generate_outfits(req: GenerateOutfitsRequest) -> GenerateOutfitsResponse:
    wardrobe_json = json.dumps([i.model_dump() for i in req.wardrobe], ensure_ascii=False)
    days_json = json.dumps([d.model_dump() for d in req.days], ensure_ascii=False)
    prompt = (
        "WARDROBE (use only these ids):\n"
        f"{wardrobe_json}\n\n"
        "DAYS (date, city, activities, weather):\n"
        f"{days_json}\n\n"
        "Produce one outfit per day."
    )
    data = await _generate([{"text": prompt}], _OUTFITS_SCHEMA, _OUTFITS_SYSTEM)
    return GenerateOutfitsResponse.model_validate(data)
