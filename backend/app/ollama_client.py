"""Local Ollama provider — a fully free, offline alternative to Claude/Gemini.

Runs an open-source vision model on this machine via the Ollama server (default
http://localhost:11434). Implements the same three tasks as the other providers
with an identical interface so ``main`` can select it transparently.

Ollama supports structured output by passing a JSON Schema in the ``format``
field, so we hand it the *same* schemas the Claude path uses (no dialect
translation needed, unlike Gemini). Images are passed as base64 in the chat
message's ``images`` list.

Vision models take only images, not PDFs — an itinerary PDF is rejected with a
clear message asking for an image/screenshot instead (the cloud providers handle
PDFs natively).
"""
from __future__ import annotations

import base64
import json

import httpx

# Reuse the exact prompts + schemas from the Claude path so behaviour matches.
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

# Local inference (especially the first call, which loads the model into memory)
# can be slow, so timeouts are generous.
_TIMEOUT = httpx.Timeout(600.0, connect=10.0)


async def _chat(system: str, user_text: str, schema: dict, images: list[str] | None = None) -> dict:
    """One structured-output chat turn against the local Ollama server."""
    s = get_settings()
    user_msg: dict = {"role": "user", "content": user_text}
    if images:
        user_msg["images"] = images
    body = {
        "model": s.ollama_model,
        "messages": [{"role": "system", "content": system}, user_msg],
        "format": schema,          # Ollama constrains output to this JSON Schema.
        "stream": False,
        "options": {"temperature": 0},
    }
    url = f"{s.ollama_host.rstrip('/')}/api/chat"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=body)
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Can't reach the local Ollama server at {s.ollama_host}. "
            "Is it running (`ollama serve`)?"
        ) from exc

    if resp.status_code != 200:
        raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
    content = (resp.json().get("message") or {}).get("content", "")
    if not content.strip():
        raise RuntimeError("Empty response from the local model.")
    return json.loads(content)


def _image_b64(file_bytes: bytes) -> str:
    return base64.standard_b64encode(file_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# 1. Itinerary parsing
# ---------------------------------------------------------------------------
async def parse_itinerary(file_bytes: bytes, content_type: str, filename: str) -> ParsedItinerary:
    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
    if is_pdf:
        raise RuntimeError(
            "The local model reads images, not PDFs. Please upload a photo or "
            "screenshot of your itinerary instead."
        )
    data = await _chat(
        _ITINERARY_SYSTEM,
        "Extract the travel itinerary from this image.",
        _ITINERARY_SCHEMA,
        images=[_image_b64(file_bytes)],
    )
    return ParsedItinerary.model_validate(data)


# ---------------------------------------------------------------------------
# 2. Clothing recognition
# ---------------------------------------------------------------------------
async def recognize_clothing(file_bytes: bytes, content_type: str) -> RecognizeResponse:
    data = await _chat(
        _RECOGNIZE_SYSTEM,
        "Identify and categorize the clothing in this photo.",
        _RECOGNIZE_SCHEMA,
        images=[_image_b64(file_bytes)],
    )
    return RecognizeResponse.model_validate(data)


# ---------------------------------------------------------------------------
# 3. Outfit generation (text only)
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
    data = await _chat(_OUTFITS_SYSTEM, prompt, _OUTFITS_SCHEMA)
    return GenerateOutfitsResponse.model_validate(data)
