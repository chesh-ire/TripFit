# TripFit

A weather-based travel outfit planner. Upload a trip itinerary and photos of
your clothes; TripFit extracts your destinations & dates, fetches the weather,
and builds a weather-appropriate outfit for each day **using only clothes you
actually own**, plus a consolidated packing list.

This repository contains two parts:

```
MyApplication4/
├── app/        → Android client (Kotlin, Jetpack Compose, MVVM + Room)
└── backend/    → FastAPI proxy that holds the Anthropic key (Claude) + weather
```

## Architecture

```
┌────────────────────────┐        HTTPS/JSON        ┌───────────────────────────┐
│  Android app (Compose)  │ ───────────────────────▶ │  FastAPI backend          │
│  • Room (trips,wardrobe)│                           │  • /itinerary/parse       │
│  • Retrofit + kotlinx   │                           │  • /wardrobe/recognize    │  ──▶ Claude (vision + structured output)
│  • Coil (thumbnails)    │ ◀─────────────────────── │  • /outfits/generate      │
│  • MVVM view models     │        validated JSON     │  • /weather               │  ──▶ Open-Meteo (keyless)
└────────────────────────┘                           └───────────────────────────┘
```

**The Anthropic API key lives only on the backend** — it is never shipped in the
app (per the brief's security goal). The backend also *validates* the AI's output
(every outfit item ID must exist in the user's wardrobe; coverage and basic
weather-safety rules are enforced in code, not left to the model alone).

### AI tasks (all via Claude `claude-opus-4-8`, structured JSON output)

| Task | Endpoint | Input → Output |
| ---- | -------- | -------------- |
| Itinerary parsing | `POST /api/itinerary/parse` | PDF/image → days (date, city, activities) |
| Clothing recognition | `POST /api/wardrobe/recognize` | photo → categorized items |
| Outfit generation | `POST /api/outfits/generate` | wardrobe + weather + activities → outfit item IDs + reasons |

Weather comes from **Open-Meteo** (no key). Within ~16 days it uses the forecast;
further out it falls back to a historical-average **estimate**, clearly flagged.

## The user journey (core vertical slice, implemented)

1. **My Trips** → New trip
2. **Create Trip** → upload itinerary (PDF/image)
3. **Review** extracted destinations/dates/activities (editable) → fetch weather
4. **My Wardrobe** → add clothing photos (AI categorizes; edit in **Item Details**)
5. **Trip Overview** → weather per day → **Generate outfits**
6. **Daily Planner** → per-day outfit from your clothes, with reasons & layering;
   regenerate, remove, or swap items
7. **Packing List** → de-duplicated, category-grouped checklist

## Running it

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate     # Python 3.11–3.13 recommended
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**No API key? It just works — in demo mode.** With no credentials configured the
server auto-runs a credential-free demo: real weather + a real rule-based outfit
engine, with a sample (editable) itinerary and sample clothing recognition.
`GET /health` reports `"mode": "demo"`.

To use **real Claude AI** instead, pick either:
- **Pay-as-you-go key** — `cp .env.example .env` and set `ANTHROPIC_API_KEY` (from
  https://console.anthropic.com/), or
- **Claude subscription** — install the `ant` CLI and run `ant auth login`; leave
  `ANTHROPIC_API_KEY` empty and the backend resolves your profile automatically.

The server switches to live mode automatically once a credential is present.
Docs at http://localhost:8000/docs.

### 2. Android app

Open the project root in Android Studio and run the `app` config, or:

```bash
./gradlew :app:assembleDebug        # or installDebug with a device/emulator
```

- **Emulator** reaches the host backend at `http://10.0.2.2:8000/` (the default
  debug `BASE_URL`).
- **Physical device**: change the debug `BASE_URL` in `app/build.gradle.kts` to
  your machine's LAN IP.

> Debug builds use cleartext HTTP for local development
> (`usesCleartextTraffic=true`). Point `BASE_URL` at an HTTPS backend for release.

## Tech notes

- **Android**: Kotlin 2.2.10 (AGP 9 built-in Kotlin), Compose (BOM 2024.09),
  Navigation-Compose, Room 2.7 (+KSP2), Retrofit + kotlinx.serialization, Coil,
  core-library desugaring (for `java.time` on minSdk 24). Manual DI via
  `AppContainer`.
- **Backend**: FastAPI + the official `anthropic` SDK + httpx.

## Known tradeoffs & next steps (not in the MVP slice)

- **Auth / multi-user**: single local profile today. Add accounts + per-user data.
- **Image storage**: wardrobe thumbnails are stored in app-internal files. For a
  real backend, upload to object storage (e.g. Supabase Storage / S3).
- **Background removal** for cleaner clothing thumbnails.
- **Favorites**, generated outfit *images*, and offline weather caching.
- Move the backend behind HTTPS + rate limiting before shipping.
