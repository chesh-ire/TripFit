# TripFit Backend

A thin FastAPI proxy that keeps the Anthropic API key server-side and powers the
TripFit Android app. It wraps three Claude (AI) tasks plus a weather lookup.

## Endpoints

| Method | Path                     | Purpose |
| ------ | ------------------------ | ------- |
| GET    | `/health`                | Liveness + whether the key is configured |
| POST   | `/api/itinerary/parse`   | multipart `file` (PDF/image) → structured trip days |
| POST   | `/api/wardrobe/recognize`| multipart `file` (image) → categorized clothing items |
| GET    | `/api/weather`           | `?city=&date=YYYY-MM-DD` → one day of weather (Open-Meteo, keyless) |
| POST   | `/api/outfits/generate`  | JSON `{days, wardrobe}` → one outfit per day (item IDs only) |

The AI only ever references item IDs that the client supplied; the server also
validates IDs, outfit coverage, and basic weather safety before responding.

## Run locally

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Interactive docs: http://localhost:8000/docs

### Demo mode vs live mode

With **no credentials** the server runs in **demo mode** automatically: real
weather, a real rule-based outfit engine, and sample (editable) itinerary +
clothing. `GET /health` → `"mode": "demo"`.

For **real Claude AI**, add a credential and restart:
- `cp .env.example .env` and set `ANTHROPIC_API_KEY` (pay-as-you-go), **or**
- `ant auth login` with a Claude subscription (leave the key empty; the SDK picks
  up your profile).

Force a mode with `TRIPFIT_MOCK=1` (demo) or `TRIPFIT_MOCK=0` (live).

## Pointing the Android app at it

- **Android emulator** reaches the host machine at `http://10.0.2.2:8000/`.
- **Physical device** on the same Wi‑Fi: use your machine's LAN IP, e.g.
  `http://192.168.1.20:8000/`.

Set the base URL in the app's `data/remote/NetworkModule.kt` (`BASE_URL`).

> The app talks to the backend over cleartext HTTP for local development
> (`usesCleartextTraffic` is enabled for the debug build). Use HTTPS in
> production.

## Notes

- Weather within ~16 days uses the Open-Meteo forecast; further out it falls
  back to a historical-average **estimate** (flagged `is_estimate: true`).
- Model defaults to `claude-opus-4-8`; override with `CLAUDE_MODEL` in `.env`.
