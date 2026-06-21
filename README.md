# Gridlock 2.0 — Parking-Induced Congestion Intelligence

Detects illegal-parking **hotspots** in Bengaluru, estimates the **latent
violation rate** (debiased for patrol coverage), and ranks locations by their
**congestion impact** — so enforcement targets where it matters, not just where
patrols already go.

## The core problem with the data

The dataset (298,450 parking violations, 10 Nov 2023 – 8 Apr 2024) is a
**patrol log, not a violation census**. We only observe violations where an
officer happened to be. Naively ranking cells by violation count just
rediscovers patrol routes.

Two structural biases we correct for:

- **Temporal.** Timestamps are UTC. Converted to IST, violations cluster
  **03:00–12:00, peaking 10–11 AM**, and are near-zero 3 PM–10 PM. Enforcement
  is a *morning shift*; the afternoon/evening — when commercial parking demand
  peaks — is a blind spot. (The widely-quoted "53% midnight–6 AM" is an artifact
  of reading the raw UTC clock.)
- **Spatial.** 50.4% of records are camera/fixed-junction (`BTP*`, unbiased);
  the rest are mobile patrol (biased toward patrolled areas). We treat these as
  two different observational processes.

## How it works

```
violations ─▶ H3 cells + 3h slots ─▶ debiasing ─▶ OSM features ─▶ LightGBM ─▶ priority score
                                       │                                         │
                                       ├─ inverse-probability weighting          ├─ latent violation rate (debiased)
                                       ├─ fixed-junction anchor                  └─ × OSM congestion impact
                                       └─ device-ID negative sampling
```

**Debiasing**
- *Inverse-probability weighting* — estimate patrol intensity per (station,
  hour) from distinct active device-days; up-weight violations seen under light
  patrol.
- *Fixed-junction anchor* — BTP camera records are unbiased; the model sees
  `is_junction` and trusts those samples more.
- *Device-ID negative sampling* — reconstruct each officer's shift from
  `(device_id, date)`; cells adjacent to where they ticketed, with no violation,
  are *patrolled-and-clean* true negatives (distinct from "never observed").

**Features (OSM, fetched free in one bbox query)**
Road class / lanes / oneway / traffic signals (supply), POI counts —
commercial, transit, institutional (demand), metro proximity, plus spatial-lag
neighbour rates (ring-1/ring-2).

**Model** — LightGBM, Tweedie objective (handles zero-inflated counts).
Validated with **spatio-temporal CV**: forward-chaining by month × geographic
quadrant holdout, so we test generalization to under-patrolled zones rather than
re-learning patrol routes.

**Outputs**
- `priority_score` = normalized latent rate × OSM road-criticality (congestion impact).
- **Blind-spot detector** — high predicted impact **and** low observed patrol.
- **Recidivist clusters** — vehicles with ≥6 violations at a concentrated location (likely fleets/auto-stands).

## Repo structure
```
model/      ML pipeline + Colab notebook (the intelligence layer)
  gridlock_pipeline.py   source of truth — runs locally and in Colab
  gridlock_colab.ipynb   generated notebook — upload to Colab to train
  make_notebook.py       regenerates the .ipynb from the .py
backend/    Legacy descriptive pipeline (Mappls live-traffic enrichment)
frontend/   React + Mappls map — priority hotspots, blind spots, recidivists
```

## Run the model (Colab — recommended)
1. Open `model/gridlock_colab.ipynb` in Colab.
2. Run the install cell, then upload the violation CSV when prompted (or set
   `CSV_PATH`).
3. Run all. It writes `outputs/heatmap_points.json`, `hotspots_full.json`,
   `blindspots.json`, `recidivists.json`.
4. Download those into `frontend/public/`.

## Run the model (local)
```bash
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -r model/requirements.txt
cd model
GRIDLOCK_CSV="../jan to may police violation_anonymized791b166.csv" python gridlock_pipeline.py
# tip: GRIDLOCK_SAMPLE=0.05 for a fast smoke test; GRIDLOCK_OSM=0 to skip OSM
cp outputs/*.json ../frontend/public/
```
The `.py` and the notebook are the same code — edit the `.py`, then
`python make_notebook.py` to regenerate the notebook.

## Run the map
1. Get a Mappls token (console → app → static key) and paste it into
   `frontend/src/App.js` (`MAPPLS_TOKEN`). Tokens expire ~24h.
2. ```bash
   cd frontend && npm install && npm start
   ```

Map features: live traffic flow, raw violation heatmap, priority hotspots
(red→amber by score), blind spots (magenta ring), recidivist vehicles (blue),
and a ward "Enforcement Priorities" panel.

## Future work (deferred, by design)

- **VIIRS night-light socioeconomic layer.** Monthly VIIRS Day/Night Band
  composites (NASA Earthdata, free) rasterized over BBMP ward boundaries give a
  current-year economic-activity proxy — better than 2011 census income.
  Enables the **equity analysis**: are high-activity wards getting
  disproportionate enforcement vs low-income wards at equal road criticality?
  Deferred only because it needs Earthdata auth + raster handling; the join key
  (ward boundaries) is already wired.
- **STGCN ensemble.** A spatio-temporal graph conv net (H3 cells as nodes,
  adjacency as edges) to model congestion propagation between cells. Strictly
  more expressive than the tree model for spillover effects; best as a second
  ensemble member.
- **Shift-optimal patrol routing.** Given the next-6h hotspot predictions, solve
  a set-cover / max-coverage assignment of patrol units to cells under travel
  constraints — turning the heatmap into a one-click "plan my route" for an
  officer.
- **Weather & events features.** Hourly rainfall (free historical) and a
  holiday/event calendar to explain temporal variance.

## Data sources
- Violations CSV — organizer-provided (gitignored, not committed).
- Road network & POIs — OpenStreetMap via OSMnx/Overpass (free).
- BBMP ward boundaries — public DataMeet / Open City GeoJSON (`backend/BBMP.geojson`).
- Live traffic (optional map layer) — Mappls Web Maps SDK.
