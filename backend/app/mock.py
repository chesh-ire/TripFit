"""Credential-free demo mode.

When no Anthropic credentials are configured, the backend runs in "mock" mode so
the whole app works end to end:

  * Itinerary parsing  -> a sample multi-day trip (dates relative to today, so the
                          real weather API returns forecasts). Edit it in Review.
  * Clothing recognize -> a sample item chosen deterministically from the photo's
                          bytes (your real photo is still used as the thumbnail).
  * Outfit generation  -> a REAL rule-based engine (weather + activity aware) that
                          dresses each day from the actual wardrobe. No AI needed.

Weather is always real (Open-Meteo needs no key).
"""
from __future__ import annotations

import datetime as dt
import hashlib

from .schemas import (
    DayOutfit,
    GenerateOutfitsRequest,
    GenerateOutfitsResponse,
    OutfitWardrobeItem,
    ParsedDay,
    ParsedItinerary,
    RecognizedItem,
    RecognizeResponse,
)


# ---------------------------------------------------------------------------
# Itinerary (sample)
# ---------------------------------------------------------------------------
def sample_itinerary() -> ParsedItinerary:
    start = dt.date.today() + dt.timedelta(days=5)
    plan = [
        ("London", "United Kingdom", ["sightseeing", "dinner"]),
        ("London", "United Kingdom", ["business"]),
        ("Edinburgh", "United Kingdom", ["sightseeing", "hiking"]),
    ]
    days = [
        ParsedDay(
            date=(start + dt.timedelta(days=i)).isoformat(),
            city=city,
            country=country,
            activities=acts,
        )
        for i, (city, country, acts) in enumerate(plan)
    ]
    return ParsedItinerary(
        trip_name="Sample trip (demo mode)",
        days=days,
        warnings=[
            "Demo mode: this is a sample itinerary. Edit the dates, cities, and "
            "activities to match your real trip, or add an ANTHROPIC_API_KEY to "
            "parse your own itinerary file.",
        ],
    )


# ---------------------------------------------------------------------------
# Clothing recognition (sample catalog, chosen by image hash)
# ---------------------------------------------------------------------------
_CATALOG: list[RecognizedItem] = [
    RecognizedItem(name="White T-shirt", category="top", color="white", style="casual",
                   seasons=["warm", "mild"], occasions=["sightseeing", "casual", "beach"],
                   layer="base", warmth=1, waterproof=False),
    RecognizedItem(name="Blue Oxford shirt", category="top", color="blue", style="smart-casual",
                   seasons=["mild", "cool"], occasions=["business", "dinner", "sightseeing"],
                   layer="base", warmth=2, waterproof=False),
    RecognizedItem(name="Dark jeans", category="bottom", color="dark blue", style="casual",
                   seasons=["mild", "cool"], occasions=["sightseeing", "casual", "dinner"],
                   layer="base", warmth=2, waterproof=False),
    RecognizedItem(name="Chino trousers", category="bottom", color="beige", style="smart-casual",
                   seasons=["warm", "mild"], occasions=["business", "sightseeing"],
                   layer="base", warmth=2, waterproof=False),
    RecognizedItem(name="Summer dress", category="dress", color="floral", style="casual",
                   seasons=["warm"], occasions=["dinner", "beach", "sightseeing"],
                   layer="base", warmth=1, waterproof=False),
    RecognizedItem(name="Rain jacket", category="outerwear", color="navy", style="casual",
                   seasons=["cool", "cold", "mild"], occasions=["sightseeing", "hiking"],
                   layer="outer", warmth=3, waterproof=True),
    RecognizedItem(name="Wool coat", category="outerwear", color="grey", style="smart-casual",
                   seasons=["cold"], occasions=["business", "dinner", "sightseeing"],
                   layer="outer", warmth=5, waterproof=False),
    RecognizedItem(name="Sneakers", category="shoes", color="white", style="casual",
                   seasons=["warm", "mild", "cool"], occasions=["sightseeing", "casual"],
                   layer="footwear", warmth=2, waterproof=False),
    RecognizedItem(name="Hiking boots", category="shoes", color="brown", style="sporty",
                   seasons=["cool", "cold"], occasions=["hiking"],
                   layer="footwear", warmth=3, waterproof=True),
    RecognizedItem(name="Leather loafers", category="shoes", color="black", style="formal",
                   seasons=["mild", "cool"], occasions=["business", "dinner"],
                   layer="footwear", warmth=2, waterproof=False),
    RecognizedItem(name="Wool scarf", category="accessory", color="charcoal", style="casual",
                   seasons=["cold"], occasions=["sightseeing", "business"],
                   layer="accessory", warmth=4, waterproof=False),
]


def recognize(image_bytes: bytes) -> RecognizeResponse:
    idx = int(hashlib.sha256(image_bytes).hexdigest(), 16) % len(_CATALOG)
    item = _CATALOG[idx].model_copy()
    return RecognizeResponse(items=[item])


# ---------------------------------------------------------------------------
# Outfit generation (real rule-based engine — used in demo mode)
# ---------------------------------------------------------------------------
def _pick(
    candidates: list[OutfitWardrobeItem],
    activities: list[str],
    *,
    prefer_waterproof: bool,
    want_warm: bool,
    usage: dict[str, int],
) -> OutfitWardrobeItem | None:
    if not candidates:
        return None
    acts = set(activities)

    def score(i: OutfitWardrobeItem) -> float:
        s = len(set(i.occasions) & acts) * 3.0
        if prefer_waterproof and i.waterproof:
            s += 4.0  # strong preference for rain protection when rain is likely
        s += i.warmth if want_warm else (5 - i.warmth) * 0.2
        s -= usage.get(i.id, 0) * 1.5  # reuse penalty -> variety across days
        return s

    best = max(candidates, key=score)
    usage[best.id] = usage.get(best.id, 0) + 1
    return best


def _by_category(wardrobe: list[OutfitWardrobeItem], category: str) -> list[OutfitWardrobeItem]:
    return [i for i in wardrobe if i.category == category]


def build_outfits(req: GenerateOutfitsRequest) -> GenerateOutfitsResponse:
    usage: dict[str, int] = {}
    outfits: list[DayOutfit] = []

    for day in req.days:
        w = day.weather
        temp = w.temp_max_c if w else 18.0
        rain = w.precipitation_probability if w else 0
        very_cold = temp <= 5
        cold = temp <= 12
        hot = temp >= 25
        want_warm = cold
        prefer_wp = rain >= 50

        # Exclude very warm items when it's hot (unless nothing else exists).
        def usable(items: list[OutfitWardrobeItem]) -> list[OutfitWardrobeItem]:
            if not hot:
                return items
            filtered = [i for i in items if i.warmth < 4]
            return filtered or items

        chosen: list[OutfitWardrobeItem] = []
        tops = usable(_by_category(req.wardrobe, "top"))
        bottoms = usable(_by_category(req.wardrobe, "bottom"))
        dresses = usable(_by_category(req.wardrobe, "dress"))
        shoes = usable(_by_category(req.wardrobe, "shoes"))
        outer = _by_category(req.wardrobe, "outerwear")
        acc = _by_category(req.wardrobe, "accessory")

        # Dress only when warm-ish and the day suits it; otherwise top + bottom.
        acts = set(day.activities)
        use_dress = bool(dresses) and not cold and (acts & {"dinner", "beach"} or not bottoms)
        if use_dress:
            d = _pick(dresses, day.activities, prefer_waterproof=False, want_warm=False, usage=usage)
            if d:
                chosen.append(d)
        else:
            top = _pick(tops, day.activities, prefer_waterproof=False, want_warm=want_warm, usage=usage)
            bottom = _pick(bottoms, day.activities, prefer_waterproof=False, want_warm=want_warm, usage=usage)
            chosen += [c for c in (top, bottom) if c]

        s = _pick(shoes, day.activities, prefer_waterproof=prefer_wp, want_warm=want_warm, usage=usage)
        if s:
            chosen.append(s)

        if cold or prefer_wp:
            o = _pick(outer, day.activities, prefer_waterproof=prefer_wp, want_warm=cold, usage=usage)
            if o:
                chosen.append(o)

        if very_cold and acc:
            a = _pick(acc, day.activities, prefer_waterproof=False, want_warm=True, usage=usage)
            if a:
                chosen.append(a)

        # Build human-readable notes.
        bits = []
        if w:
            bits.append(f"{round(w.temp_min_c)}–{round(w.temp_max_c)}°C")
            if rain >= 50:
                bits.append("rain likely")
        if day.activities:
            bits.append("for " + ", ".join(day.activities))
        reason = "Picked " + (" + ".join(i.name for i in chosen) if chosen else "an outfit")
        if bits:
            reason += " — " + ", ".join(bits) + "."
        layering = (
            "Layer up with your outer layer and an accessory; it's cold." if very_cold
            else "Bring a layer you can add or remove." if cold
            else "Add rain protection — carry the waterproof piece." if prefer_wp
            else "Light, breathable layers are enough."
        )

        outfits.append(
            DayOutfit(
                date=day.date,
                item_ids=[i.id for i in chosen],
                reason=reason,
                layering=layering,
            )
        )

    return GenerateOutfitsResponse(outfits=outfits)
