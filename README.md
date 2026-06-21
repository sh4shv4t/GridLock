# Gridlock 2.0 — Parking-Induced Congestion Intelligence

Detects illegal-parking hotspots in Bengaluru and ranks them by their **actual
impact on traffic flow** (not just raw violation counts), using MapmyIndia
(Mappls) traffic data. Built for the Gridlock 2.0 hackathon.

## What it does
A location with fewer violations but severe congestion can matter more than a
high-violation spot on an empty road. We quantify that:

```
impact_score = (violation_count / max_violation_count) × congestion_ratio
```

where `congestion_ratio = live-traffic travel time ÷ baseline travel time`
(from MapmyIndia's Distance Matrix APIs).

## Repo structure
```
backend/    Python data pipeline (run once to produce the JSON the map reads)
frontend/   React app — the interactive map (MapmyIndia Web Maps SDK)
```

## Setup

### 1. Get a Mappls token
Create an app in the Mappls console and copy the static key.
Paste it into **both** `backend/build_data.py` and `frontend/src/App.js`
(replace `YOUR_MAPPLS_TOKEN_HERE`). Tokens expire ~24h — regenerate if the map
loads blank or REST calls return 401.

### 2. Backend — build the data
Place the violation CSV in `backend/`, then:
```bash
cd backend
pip install -r requirements.txt
python build_data.py     # writes heatmap_points.json + hotspots_full.json
python assign_wards.py    # adds BBMP ward_no + ward_name (needs BBMP.geojson)
```
Run order matters: `build_data.py` → `assign_wards.py`.
Run at a weekday peak hour (~9–10 AM or 6–8 PM) for strong congestion contrast.

### 3. Move the data to the frontend
Copy `heatmap_points.json` and `hotspots_full.json` into `frontend/public/`.

### 4. Frontend — run the map
```bash
cd frontend
npm install
npm start
```

## Features
- Live MapmyIndia traffic flow layer (`traffic: true`)
- Citywide violation heatmap (~10k points)
- Top-100 impact hotspots, colored by impact (red = high)
- Ward panel ("Enforcement Priorities") — click a BBMP ward to filter its targets
- Per-hotspot detail panel

## APIs used
Web Maps SDK · Reverse Geocode · Distance Matrix (+ ETA) · HeatmapLayer · Circle.
Gated on our tier (organizer ask): Snap to Road V2, Driving Range Polygon.

## Notes
- Raw CSV is organizer-provided and not committed (see `.gitignore`).
- BBMP ward boundaries: public DataMeet GeoJSON.
