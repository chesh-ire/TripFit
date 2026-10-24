"""Pydantic request/response schemas shared across the API.

These mirror the Kotlin data classes in the Android client. Keep field names in
sync (snake_case here; the client uses @SerialName / matching property names).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Itinerary parsing
# ---------------------------------------------------------------------------
class ParsedDay(BaseModel):
    date: str = Field(description="ISO date YYYY-MM-DD")
    city: str
    country: str | None = None
    activities: list[str] = Field(default_factory=list)


class ParsedItinerary(BaseModel):
    trip_name: str | None = None
    days: list[ParsedDay] = Field(default_factory=list)
    # Non-fatal problems the client should surface for review.
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Wardrobe recognition
# ---------------------------------------------------------------------------
class RecognizedItem(BaseModel):
    name: str
    category: str = Field(description="top | bottom | dress | outerwear | shoes | accessory")
    color: str | None = None
    style: str | None = None  # casual | smart-casual | formal | sporty
    seasons: list[str] = Field(default_factory=list)  # warm | mild | cool | cold
    occasions: list[str] = Field(default_factory=list)  # sightseeing | hiking | beach | dinner | business
    layer: str | None = None  # base | mid | outer | footwear | accessory
    warmth: int = Field(default=2, description="1 (very light) .. 5 (very warm)")
    waterproof: bool = False


class RecognizeResponse(BaseModel):
    items: list[RecognizedItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------
class DayWeather(BaseModel):
    date: str
    city: str
    temp_min_c: float
    temp_max_c: float
    feels_like_c: float
    precipitation_probability: int = 0
    wind_kph: float = 0.0
    humidity: int | None = None
    condition: str = "unknown"  # clear | cloudy | rain | snow | ...
    is_estimate: bool = False  # true when beyond the forecast horizon


# ---------------------------------------------------------------------------
# Outfit generation
# ---------------------------------------------------------------------------
class OutfitWardrobeItem(BaseModel):
    """Trimmed wardrobe item the client sends for outfit generation."""

    id: str
    name: str
    category: str
    color: str | None = None
    style: str | None = None
    warmth: int = 2
    waterproof: bool = False
    occasions: list[str] = Field(default_factory=list)


class OutfitDayRequest(BaseModel):
    date: str
    city: str
    activities: list[str] = Field(default_factory=list)
    weather: DayWeather | None = None


class GenerateOutfitsRequest(BaseModel):
    days: list[OutfitDayRequest]
    wardrobe: list[OutfitWardrobeItem]


class DayOutfit(BaseModel):
    date: str
    item_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    layering: str = ""
    # Populated by the server when the AI could not fully dress a day.
    warnings: list[str] = Field(default_factory=list)


class GenerateOutfitsResponse(BaseModel):
    outfits: list[DayOutfit] = Field(default_factory=list)
