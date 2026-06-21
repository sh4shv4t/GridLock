# Gridlock CIS API

Thin, read-only FastAPI service over the precomputed **Congestion Impact Scores**
(`cis_hotspots.json`, produced by `model/gridlock_pipeline.py`). Serves the exact
CIS contract per hotspot. Optionally refreshes the **ECS** component live from
Mappls when a token is set (with caching + `low_confidence` fallback).

## Run
```bash
cd api
pip install -r requirements.txt
# optional overrides:
export GRIDLOCK_DATA=../frontend/public/cis_hotspots.json   # JSON source (default)
export GRIDLOCK_DB=../model/gridlock.db                     # serve from SQLite instead (model/db.py)
export GRIDLOCK_BASELINE=ecs_baseline.json                  # rolling 8-week ECS baseline store
export MAPPLS_TOKEN=...                                     # enables ?live=true ECS refresh
uvicorn main:app --reload --port 8000
```
Interactive docs: http://localhost:8000/docs

**Serving backends:** set `GRIDLOCK_DB` to a SQLite file populated by
`python model/db.py ingest` (kept fresh by `model/scheduler.py`) to serve from
storage; otherwise it reads JSON. `/health` reports the active backend. With
`MAPPLS_TOKEN` set, `?live=true` refreshes ECS from Mappls Flow minus the rolling
8-week baseline (`baseline_store.py`), staying `low_confidence` until it warms up.

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | record count, data source, whether live ECS is enabled |
| GET | `/hotspots?class=High&min_cis=50&limit=100` | filtered list, ranked by CIS |
| GET | `/hotspots/{hotspot_id}` | single hotspot (the CIS contract) |
| GET | `/hotspots/{hotspot_id}?live=true` | recompute **ECS only** from Mappls live speed; falls back to cache → stored proxy |
| POST | `/reload` | hot-reload the JSON after a new pipeline run |

## Example response
```json
{
  "hotspot_id": "89618925c03ffff",
  "window_start": "2024-04-08T10:00:00",
  "cis": 60.6,
  "class": "High",
  "confidence": 0.65,
  "components": {
    "violation_load": 28.8, "carriageway_obstruction": 10.3,
    "excess_congestion": 20.0, "recurrence": 1.5
  },
  "explanation": [
    "Violation load +28.8 pts (debiased load, mostly CAR, PASSENGER AUTO)",
    "Excess congestion +20.0 pts (live-traffic proxy, road near capacity)",
    "Carriageway obstruction +10.3 pts (~1 of 1 lanes blocked)",
    "Recurrence +1.5 pts (3 of last 30 days)"
  ],
  "low_confidence": true
}
```

## Notes
- The heavy scoring (Steps 1–3 + Phase-1 classifier) lives in the batch pipeline;
  this API does not recompute it per request. `?live=true` only refreshes ECS.
- `low_confidence: true` means ECS came from the OSM proxy or a cached value
  rather than a fresh live speed reading.
- Phase-2 (trained classifier) swaps in at the pipeline layer behind the same
  contract — no API change needed.
