"""All Claude (Anthropic) calls live here.

Three AI tasks, each using Claude vision/text with structured JSON output so the
responses are machine-parseable and validated against a schema:

  * parse_itinerary       — PDF/image of a travel itinerary -> trip days
  * recognize_clothing    — photo of clothing item(s)       -> categorized items
  * generate_outfits      — wardrobe + weather + activities  -> outfits (item IDs)

The model is instructed to only ever reference item IDs from the supplied
wardrobe; the caller (routes) additionally validates this.
"""
from __future__ import annotations

import base64
import json

from anthropic import AsyncAnthropic

from .config import get_settings
from .schemas import (
    GenerateOutfitsRequest,
    GenerateOutfitsResponse,
    ParsedItinerary,
    RecognizeResponse,
)

IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    """Lazily build the client. With an explicit key we pass it; otherwise we let
    the SDK resolve credentials from the environment (ANTHROPIC_AUTH_TOKEN or an
    `ant auth login` profile)."""
    global _client
    if _client is None:
        s = get_settings()
        _client = AsyncAnthropic(api_key=s.anthropic_api_key) if s.anthropic_api_key else AsyncAnthropic()
    return _client


def _json_format(schema: dict) -> dict:
    return {"format": {"type": "json_schema", "schema": schema}}


async def _structured_call(content: list[dict], schema: dict, system: str) -> dict:
    """Run a single structured-output message and return the parsed JSON object."""
    resp = await _get_client().messages.create(
        model=get_settings().claude_model,
        max_tokens=4096,
        system=system,
        output_config=_json_format(schema),
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("The model declined to process this request.")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("Empty response from the model.")
    return json.loads(text)


# ---------------------------------------------------------------------------
# 1. Itinerary parsing
# ---------------------------------------------------------------------------
_ITINERARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "trip_name": {"type": ["string", "null"]},
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    "city": {"type": "string"},
                    "country": {"type": ["string", "null"]},
                    "activities": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["date", "city", "country", "activities"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["trip_name", "days", "warnings"],
}

_ITINERARY_SYSTEM = (
    "You extract structured travel itineraries from documents and images. "
    "Return one entry per travel day with the date (ISO YYYY-MM-DD), the city, "
    "the country if known, and a short list of planned activities (e.g. "
    "'sightseeing', 'hiking', 'beach', 'business meeting', 'dinner'). "
    "If a multi-day stay has no per-day breakdown, create one entry per day of "
    "that stay. If a year is missing, infer the most likely upcoming year. "
    "Add a short note to 'warnings' for anything ambiguous or missing so the "
    "user can review it. Never invent destinations that are not supported by the "
    "document."
)


async def parse_itinerary(file_bytes: bytes, content_type: str, filename: str) -> ParsedItinerary:
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        doc_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        media = content_type if content_type in IMAGE_MEDIA_TYPES else "image/jpeg"
        doc_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": b64},
        }

    content = [
        doc_block,
        {"type": "text", "text": "Extract the travel itinerary from this file."},
    ]
    data = await _structured_call(content, _ITINERARY_SCHEMA, _ITINERARY_SYSTEM)
    return ParsedItinerary.model_validate(data)


# ---------------------------------------------------------------------------
# 2. Clothing recognition
# ---------------------------------------------------------------------------
_RECOGNIZE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "description": "short human label, e.g. 'white linen shirt'"},
                    "category": {
                        "type": "string",
                        "enum": ["top", "bottom", "dress", "outerwear", "shoes", "accessory"],
                    },
                    "color": {"type": ["string", "null"]},
                    "style": {
                        "type": ["string", "null"],
                        "description": "casual | smart-casual | formal | sporty",
                    },
                    "seasons": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["warm", "mild", "cool", "cold"]},
                    },
                    "occasions": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["sightseeing", "hiking", "beach", "dinner", "business", "casual"],
                        },
                    },
                    "layer": {
                        "type": ["string", "null"],
                        "description": "base | mid | outer | footwear | accessory",
                    },
                    "warmth": {"type": "integer", "minimum": 1, "maximum": 5},
                    "waterproof": {"type": "boolean"},
                },
                "required": [
                    "name", "category", "color", "style", "seasons",
                    "occasions", "layer", "warmth", "waterproof",
                ],
            },
        }
    },
    "required": ["items"],
}

_RECOGNIZE_SYSTEM = (
    "You are a fashion cataloguing assistant. Identify each distinct clothing "
    "item or accessory visible in the image. For each item provide a short name, "
    "category, dominant color, style, suitable seasons (by temperature band), "
    "suitable occasions, which layer it is, a warmth rating 1-5 (1 = very light "
    "like a tee, 5 = very warm like a heavy coat), and whether it is waterproof. "
    "If multiple garments are shown, return one entry per garment. Be concise and "
    "do not guess attributes you cannot see."
)


async def recognize_clothing(file_bytes: bytes, content_type: str) -> RecognizeResponse:
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    media = content_type if content_type in IMAGE_MEDIA_TYPES else "image/jpeg"
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
        {"type": "text", "text": "Identify and categorize the clothing in this photo."},
    ]
    data = await _structured_call(content, _RECOGNIZE_SCHEMA, _RECOGNIZE_SYSTEM)
    return RecognizeResponse.model_validate(data)


# ---------------------------------------------------------------------------
# 3. Outfit generation
# ---------------------------------------------------------------------------
_OUTFITS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "outfits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "date": {"type": "string"},
                    "item_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "layering": {"type": "string"},
                },
                "required": ["date", "item_ids", "reason", "layering"],
            },
        }
    },
    "required": ["outfits"],
}

_OUTFITS_SYSTEM = (
    "You are a personal travel stylist. You build one outfit per day using ONLY "
    "the wardrobe items provided, referenced strictly by their 'id'. Rules:\n"
    "1. Match each outfit to that day's weather and planned activities.\n"
    "2. A complete outfit covers a top + bottom (or a dress), footwear, plus "
    "outerwear and accessories when the weather calls for them. Include exactly "
    "ONE pair of footwear and ONE bottom (or ONE dress) per day — never two.\n"
    "3. Never pick clothing unsuitable for the weather (no heavy coats in heat, "
    "no shorts in freezing temps). Add rain protection when rain is likely.\n"
    "4. Reuse items across days to minimise what must be packed, but avoid "
    "repeating the exact same full outfit unless the wardrobe is too small.\n"
    "5. Prefer combinations that match in color, style, and occasion.\n"
    "6. In 'reason', briefly explain why the outfit fits the day's weather and "
    "activities. In 'layering', give a short layering tip.\n"
    "Only use ids that appear in the wardrobe. Do not invent items."
)


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
    data = await _structured_call(
        [{"type": "text", "text": prompt}], _OUTFITS_SCHEMA, _OUTFITS_SYSTEM
    )
    return GenerateOutfitsResponse.model_validate(data)
