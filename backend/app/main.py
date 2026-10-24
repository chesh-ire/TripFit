"""TripFit backend — a thin proxy that keeps the Anthropic key server-side and
exposes itinerary parsing, clothing recognition, weather, and outfit generation
to the Android client.

Run:  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import claude_client, gemini_client, mock, ollama_client, weather
from .config import get_settings
from .schemas import (
    DayOutfit,
    GenerateOutfitsRequest,
    GenerateOutfitsResponse,
    ParsedItinerary,
    RecognizeResponse,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tripfit")

settings = get_settings()
PROVIDER = settings.select_provider()          # "anthropic" | "gemini" | "demo"
USE_MOCK = PROVIDER == "demo"
# The live AI module exposes parse_itinerary / recognize_clothing / generate_outfits.
AI = {
    "anthropic": claude_client,
    "gemini": gemini_client,
    "ollama": ollama_client,
}.get(PROVIDER)
if USE_MOCK:
    log.warning(
        "TripFit running in DEMO MODE (no AI credentials). Itinerary and clothing "
        "use sample data; outfits use the rule-based engine; weather is real."
    )
else:
    log.info("TripFit running in LIVE mode via provider=%s", PROVIDER)

app = FastAPI(title="TripFit API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB
ALLOWED_UPLOAD_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


def _require_ai() -> None:
    """In demo mode no credentials are needed. Otherwise the selected provider
    must have its credential and a live module wired up."""
    if USE_MOCK:
        return
    if AI is None or (PROVIDER == "anthropic" and not settings.has_anthropic) or (
        PROVIDER == "gemini" and not settings.has_gemini
    ):
        raise HTTPException(503, f"Server has no credentials for provider '{PROVIDER}'.")


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 15 MB).")
    return data


@app.get("/health")
async def health() -> dict:
    model = {
        "anthropic": settings.claude_model,
        "gemini": settings.gemini_model,
        "ollama": settings.ollama_model,
    }.get(PROVIDER)
    return {
        "status": "ok",
        "mode": "demo" if USE_MOCK else "live",
        "provider": PROVIDER,
        "model": model,
        "credentials_configured": settings.has_credentials,
    }


# ---------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------
@app.post("/api/itinerary/parse", response_model=ParsedItinerary)
async def parse_itinerary(file: UploadFile = File(...)) -> ParsedItinerary:
    _require_ai()
    ctype = file.content_type or ""
    if ctype not in ALLOWED_UPLOAD_TYPES and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, f"Unsupported file type: {ctype!r}. Use PDF or an image.")
    data = await _read_upload(file)
    if USE_MOCK:
        return mock.sample_itinerary()
    try:
        return await AI.parse_itinerary(data, ctype, file.filename or "upload")
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the client
        log.exception("itinerary parse failed")
        raise HTTPException(502, f"Could not parse itinerary: {exc}") from exc


# ---------------------------------------------------------------------------
# Wardrobe recognition
# ---------------------------------------------------------------------------
@app.post("/api/wardrobe/recognize", response_model=RecognizeResponse)
async def recognize(file: UploadFile = File(...)) -> RecognizeResponse:
    _require_ai()
    ctype = file.content_type or ""
    if not ctype.startswith("image/"):
        raise HTTPException(415, "Please upload an image of your clothing.")
    data = await _read_upload(file)
    if USE_MOCK:
        return mock.recognize(data)
    try:
        return await AI.recognize_clothing(data, ctype)
    except Exception as exc:  # noqa: BLE001
        log.exception("recognition failed")
        raise HTTPException(502, f"Could not recognize clothing: {exc}") from exc


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------
@app.get("/api/weather")
async def get_weather(city: str, date: str):
    try:
        return await weather.get_weather(city, date)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("weather lookup failed")
        raise HTTPException(502, f"Weather lookup failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Outfit generation (with server-side validation)
# ---------------------------------------------------------------------------
# Categories you can only wear one of in a single outfit.
_EXCLUSIVE_CATEGORIES = {"shoes", "bottom", "dress"}


def _validate_outfits(
    req: GenerateOutfitsRequest, ai: GenerateOutfitsResponse
) -> GenerateOutfitsResponse:
    """Enforce invariants the model may get wrong: every id must exist, every day
    must be covered, and basic weather/coverage sanity checks."""
    by_id = {i.id: i for i in req.wardrobe}
    required_dates = [d.date for d in req.days]
    ai_by_date = {o.date: o for o in ai.outfits}

    validated: list[DayOutfit] = []
    for day in req.days:
        outfit = ai_by_date.get(day.date) or DayOutfit(date=day.date)
        warnings: list[str] = []

        # Drop any hallucinated ids.
        in_wardrobe = [i for i in outfit.item_ids if i in by_id]
        if len(in_wardrobe) != len(outfit.item_ids):
            warnings.append("Some suggested items were not in your wardrobe and were removed.")

        # You can only wear one of these at a time — keep the first, drop extras
        # (e.g. the model picking both boots and heels for one day).
        exclusive_seen: set[str] = set()
        valid_ids: list[str] = []
        for i in in_wardrobe:
            cat = by_id[i].category
            if cat in _EXCLUSIVE_CATEGORIES:
                if cat in exclusive_seen:
                    continue
                exclusive_seen.add(cat)
            valid_ids.append(i)

        items = [by_id[i] for i in valid_ids]
        cats = {i.category for i in items}

        # Coverage: need a top+bottom or a dress, plus shoes.
        has_bottom_cover = "dress" in cats or ({"top", "bottom"} <= cats)
        if not has_bottom_cover:
            warnings.append("Outfit is missing a top/bottom or dress.")
        if "shoes" not in cats:
            warnings.append("Outfit is missing footwear.")

        # Rule-based weather safety (don't rely on the model alone).
        w = day.weather
        if w is not None:
            if w.precipitation_probability >= 50 and not any(i.waterproof for i in items):
                warnings.append("Rain is likely but no waterproof item was selected.")
            if w.temp_max_c <= 5 and not any(i.warmth >= 4 for i in items):
                warnings.append("It will be cold but no warm layer was selected.")
            if w.temp_min_c >= 25 and any(i.warmth >= 4 for i in items):
                warnings.append("It will be hot; a very warm item may be unnecessary.")

        validated.append(
            DayOutfit(
                date=day.date,
                item_ids=valid_ids,
                reason=outfit.reason,
                layering=outfit.layering,
                warnings=warnings,
            )
        )

    return GenerateOutfitsResponse(outfits=validated)


def _remap_wardrobe_ids(req: GenerateOutfitsRequest) -> tuple[GenerateOutfitsRequest, dict[str, str]]:
    """Give the model short, easy-to-echo ids (w1, w2, …) instead of UUIDs.

    Small local models frequently fail to copy long UUIDs back verbatim, so every
    suggested id misses the wardrobe and gets dropped. Short ids fix that; we map
    the model's picks back to the real ids afterwards.
    """
    id_map: dict[str, str] = {}
    short_items = []
    for idx, item in enumerate(req.wardrobe, 1):
        short = f"w{idx}"
        id_map[short] = item.id
        short_items.append(item.model_copy(update={"id": short}))
    return req.model_copy(update={"wardrobe": short_items}), id_map


def _is_complete(item_ids: list[str], by_id: dict) -> bool:
    cats = {by_id[i].category for i in item_ids if i in by_id}
    has_cover = "dress" in cats or {"top", "bottom"} <= cats
    return has_cover and "shoes" in cats


@app.post("/api/outfits/generate", response_model=GenerateOutfitsResponse)
async def generate_outfits(req: GenerateOutfitsRequest) -> GenerateOutfitsResponse:
    _require_ai()
    if not req.wardrobe:
        raise HTTPException(400, "Wardrobe is empty — add some clothes first.")
    if not req.days:
        raise HTTPException(400, "No trip days supplied.")

    # The rule-based engine is deterministic and always produces a valid outfit;
    # use it directly in demo mode and as a per-day safety net in live mode.
    baseline = mock.build_outfits(req)

    if USE_MOCK:
        return _validate_outfits(req, baseline)

    try:
        short_req, id_map = _remap_wardrobe_ids(req)
        raw = await AI.generate_outfits(short_req)
    except Exception as exc:  # noqa: BLE001
        log.exception("outfit generation failed; falling back to rule-based engine")
        return _validate_outfits(req, baseline)

    # Map the model's short ids back to the real wardrobe ids.
    for o in raw.outfits:
        o.item_ids = [id_map[i] for i in o.item_ids if i in id_map]

    # For any day the model left incomplete (no top/bottom/dress or no shoes),
    # substitute the rule-based outfit so every day is actually wearable.
    by_id = {i.id: i for i in req.wardrobe}
    raw_by_date = {o.date: o for o in raw.outfits}
    base_by_date = {o.date: o for o in baseline.outfits}
    merged: list[DayOutfit] = []
    for day in req.days:
        llm = raw_by_date.get(day.date)
        if llm is not None and _is_complete(llm.item_ids, by_id):
            merged.append(llm)
        elif day.date in base_by_date:
            merged.append(base_by_date[day.date])
        elif llm is not None:
            merged.append(llm)

    return _validate_outfits(req, GenerateOutfitsResponse(outfits=merged))
