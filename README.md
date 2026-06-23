# 🚦 Gridlock 2.0 — Parking-Induced Congestion Intelligence

![Status](https://img.shields.io/badge/status-hackathon%20build-orange)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![LightGBM](https://img.shields.io/badge/LightGBM-tweedie-9ACD32)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![FastAPI](https://img.shields.io/badge/FastAPI-read--only-009688?logo=fastapi&logoColor=white)
![Mappls](https://img.shields.io/badge/Maps-Mappls%20SDK-EE2737)
![OpenStreetMap](https://img.shields.io/badge/features-OpenStreetMap-7EBC6F?logo=openstreetmap&logoColor=white)

> Detects illegal-parking **hotspots** in Bengaluru, estimates the **latent
> violation rate** (debiased for patrol coverage), scores each location's
> **Congestion Impact (CIS, 0–100)** with a plain-language explanation, and
> flags **enforcement blind spots** — so patrols target where it matters, not
> just where they already go.

🎥 **[Watch the demo walkthrough](https://www.loom.com/share/12ae4597fb9f46b9b7469817b28bc647)**

---

## 📸 The product

**Citywide admin console — debiased priority hotspots.** The full enforcement view:
298k violations reduced to the latent, patrol-corrected rate × congestion impact,
with blind spots, recidivist clusters, ward priorities, and a debiased ⇄ raw toggle.

![Admin view — debiased citywide hotspots](docs/screenshot-admin-debiased.png)

**"My area" view — scoped to one neighbourhood.** The same engine at citizen
altitude: pick your area, see only your hotspots, your blind spots, and how your
ward ranks — deliberately sparse, no vehicle plates.

![My area view — Jakkasandra](docs/screenshot-my-area.jpeg)

---

## ⚡ Quick start (run the site)

The map already ships with precomputed data in `frontend/public/`, so you only
need the frontend to see the demo.

```bash
# 1. Map (required)
cd frontend
npm install                       # first time only
cp .env.example .env.local        # then paste your Mappls token into .env.local
npm start                         # opens http://localhost:3000
```
> The **Mappls token** is read from the environment, not the source. Put
> `REACT_APP_MAPPLS_TOKEN=...` in `frontend/.env.local` (gitignored) for local dev,
> or set it in your host's env vars (e.g. Vercel → Settings → Environment Variables).
> Without it the map loads blank. Tokens expire ~24h — regenerate at apis.mappls.com.
> (CRA only reads `REACT_APP_*` vars at **build/start** time, so restart `npm start`
> after editing `.env.local`.)

```bash
# 2. CIS API (optional — serves the score contract per hotspot)
cd api
pip install -r requirements.txt
uvicorn main:app --port 8000      # docs at http://localhost:8000/docs
```

```bash
# 3. Retrain / regenerate data (optional)
#    Open model/gridlock_colab.ipynb in Colab → Run all → download outputs/*.json
#    into frontend/public/.  Or locally:
python -m venv .venv && . .venv/Scripts/activate
pip install -r model/requirements.txt
cd model && GRIDLOCK_CSV="../<violations>.csv" python gridlock_pipeline.py
cp outputs/*.json ../frontend/public/
```

---

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
model/      ML pipeline + Colab notebooks (the intelligence layer)
  gridlock_pipeline.py   source of truth — debiased model + CIS engine
  gridlock_colab.ipynb   generated notebook — upload to Colab to train
  stgcn_pipeline.py      STGCN ensemble (graph conv + GRU); stgcn_colab.ipynb
  patrol_routing.py      shift-optimal patrol routes (greedy set cover)
  cis_phase2_train.py    Phase-2 calibrated classifier training harness
  baseline_store.py      rolling 8-week ECS baseline accumulator
  db.py / scheduler.py   SQLite persistence + interval recompute
  viirs_equity.py        ward-level equity analysis (VIIRS radiance optional)
  socioeconomic_insights.py  the "why" (aggregate): income / social-centre / infra drivers
  hotspot_interpreter.py the "why" (per hotspot): LLM reason from place/infra/income evidence
  make_notebook.py       regenerates .ipynb from any cell-marked .py
api/        Thin read-only FastAPI service over the CIS scores (+ optional DB, live ECS)
backend/    Legacy descriptive pipeline (Mappls live-traffic enrichment)
frontend/   React + Mappls map — priority hotspots, blind spots, recidivists, CIS
```

## Congestion Impact Score (CIS)
Each hotspot gets an explainable **0–100 CIS** and a Low/Medium/High/Critical
class, composed of four stored, weighted subscores:

`CIS = 100 × (0.30·VLS + 0.20·COS + 0.35·ECS + 0.15·RPS)`

- **VLS** (violation load) — the **debiased latent rate** × mean violation
  severity (vehicle type × offence code), so it doesn't re-inherit patrol bias.
- **COS** (carriageway obstruction) — parked-vehicle width × concurrency ÷ OSM
  road width.
- **ECS** (excess congestion) — live speed deficit vs baseline. No live feed in
  batch, so it uses an OSM demand×capacity proxy flagged `low_confidence`; a
  Mappls Flow feed drops in via the `ECSProvider` interface unchanged.
- **RPS** (recurrence) — days with a violation in the trailing 30.

The four weighted point-contributions are stored and rendered as the
explanation. A **Phase-2** trained classifier (LightGBM + SHAP + calibration)
swaps in behind the same contract once ≥3 months of measured-delay outcomes
exist — interface stubbed in the pipeline. Adapted to this dataset:
`duration_factor` is dropped (`closed_datetime` is 100% NULL) and ECS uses the
proxy described above.

Serve it: `cd api && pip install -r requirements.txt && uvicorn main:app --port 8000`
(`GET /hotspots/{id}` returns the contract; see `api/README.md`).

## Why these hotspots? — socio-economic drivers
A hotspot map answers *where*; this answers *why*. `socioeconomic_insights.py`
joins each hotspot to proxies for three socio-economic dimensions and quantifies
how each relates to parking pressure and congestion impact:

- **Economic activity / income** — commercial-POI density (and VIIRS night-light
  radiance when a raster is supplied via `viirs_equity.py`).
- **Proximity to social centres** — institutional POIs (schools, hospitals,
  places of worship), transit, and metro stations within 500 m.
- **Infrastructure** — OSM road capacity (throughput × lanes).

Findings on the full data (`insights.json`):

| Hotspots near… | CIS vs. elsewhere |
|---|---|
| Commercial centres | **2.84×** (12.2 vs 4.3) |
| Transit hubs / metro ≤500 m | **2.61×** (13.9 vs 5.3) |
| Schools / hospitals / worship | **1.99×** (10.8 vs 5.5) |

Strongest single correlate of congestion impact is **road capacity** (r = +0.45
with CIS), then metro proximity (+0.38) and commercial density (+0.34) — i.e.
illegal parking bites hardest where high-capacity roads meet dense activity. The
**equity question** (are richer wards over- or under-enforced at equal road
criticality?) plugs in directly once a VIIRS radiance layer is added — the ward
join is already wired (`viirs_equity.py`).

### Per-hotspot interpretability (the "why" for *this* corner)
`socioeconomic_insights.py` explains the *aggregate* pattern; `hotspot_interpreter.py`
explains **one hotspot at a time**. For each high-CIS cell it assembles a structured
evidence profile and turns it into a plain-language hypothesis:

- **Place character** — named OSM POIs within 300 m (temple, mall, market, school,
  hospital, metro, office) and a dominant "primary type".
- **Infrastructure** — road class / lanes / surface / street-lighting, plus the
  demand-vs-capacity ratio (is the road under-provisioned for the activity around it?).
- **Income proxy** — commercial-POI density percentile, blended with VIIRS ward
  radiance when a raster is supplied. Flagged a *proxy* — the data has no per-capita income.

The reason text is synthesised by **Gemini** (set `GEMINI_API_KEY`), grounded only in
that evidence. With no key — or on any API error — it falls back to a deterministic
rule-based explanation, so it runs free and offline like the rest of the pipeline (same
swappable pattern as `ECSProvider`). Output: `hotspot_interpretations.json`.

```bash
cd model
export GEMINI_API_KEY=...            # optional — omit for rule-based reasons
python hotspot_interpreter.py --limit 50          # add --no-llm to force rule-based
```
Example output (real data, Jayamahal): *"CIS 66.8 (High) likely because it draws
concentrated demand from a place of worship (Jayamahal Shani Mandir) nearby, and a narrow
~2-lane carriageway…"*

**Map features:** live traffic flow, raw violation heatmap, debiased/raw
before-after toggle, priority hotspots (red→amber by score), blind spots
(magenta ring), recidivist vehicles (blue), per-hotspot CIS breakdown, and a
ward "Enforcement Priorities" panel.

**Dev tips:** `GRIDLOCK_SAMPLE=0.05` for a fast local smoke test, `GRIDLOCK_OSM=0`
to skip OSM. The pipeline `.py` and the Colab notebook are the same code — edit
the `.py`, then `python model/make_notebook.py` to regenerate the notebook.

## ✅ Roadmap — done vs. left

**Done**
- [x] H3 indexing + UTC→IST temporal fix
- [x] Debiasing: IPW, fixed-junction anchor, device-ID negative sampling
- [x] OSM feature enrichment (roads, POIs, metro)
- [x] LightGBM latent-rate model + spatio-temporal CV + SHAP
- [x] Enforcement blind-spot detector + recidivist-vehicle clustering
- [x] Congestion Impact Score (Phase-1 rule classifier) + explanations
- [x] Read-only CIS API (FastAPI) + optional SQLite-backed serving
- [x] React map: hotspots, blind spots, recidivists, CIS panel, before/after toggle
- [x] **Shift-optimal patrol routing** (`patrol_routing.py`) — runs on real data
- [x] **STGCN ensemble** (`stgcn_pipeline.py` / `stgcn_colab.ipynb`) — graph conv + GRU
- [x] **Persistence + scheduler** (`db.py`, `scheduler.py`) — SQLite + interval recompute
- [x] **VIIRS equity analysis** (`viirs_equity.py`) — spatial half runs now
- [x] **Phase-2 training harness** (`cis_phase2_train.py`) — calibration + SHAP, ready to train
- [x] **8-week ECS baseline accumulator** (`baseline_store.py`) + live-ECS wiring in the API
- [x] **Socio-economic insight engine** (`socioeconomic_insights.py`) — why hotspots occur (aggregate)
- [x] **Per-hotspot interpreter** (`hotspot_interpreter.py`) — Gemini reason per hotspot, rule-based fallback

**Built, but waiting on an input only you can supply** (mechanism is in place; it
activates the moment the input exists):
- [ ] **Live Mappls Flow feed for ECS** — needs a Mappls token (`MAPPLS_TOKEN`);
      until then ECS is the OSM proxy flagged `low_confidence`
- [ ] **8-week baseline values** — needs ~8 weeks of live polling to accumulate
      (the store + decay logic are done; it can't be backfilled from history)
- [ ] **Phase-2 real labels** — needs ≥3 months of measured-delay outcomes; the
      harness trains today on a placeholder label and swaps in real ones via the
      `outcome_class` column
- [ ] **VIIRS radiance** — needs a NASA Earthdata GeoTIFF (`--viirs`); the ward
      join + equity report are done and run without it

## Future work (genuinely not started)
- **Parking-angle aware COS.** Today COS (`width × concurrency ÷ road width`)
  assumes *parallel* parking. A large share of Bengaluru violations — especially
  outside shops — are perpendicular/angled and consume 2–3× the carriageway
  depth. Add a parking-angle feature from OSM (`parking:lane` tag where present,
  else a heuristic from junction proximity + road class) so obstruction reflects
  the real footprint.
- **Recency-weighted RPS.** RPS currently counts trailing-30-day violation days
  uniformly, so a zone clean for 3 weeks then hit daily scores like one with 30
  sporadic hits. Switch to exponential decay — `RPS = Σ exp(−λ·dᵢ) / normaliser`
  (dᵢ = days since violation) — so RPS tracks recency *and* decays after a
  successful crackdown, rewarding enforcement.
- **Weather & events features.** Hourly rainfall (free historical) — rain shifts
  both violation rate (patrols shelter) and congestion impact (wet roads lose
  effective speed per unit blockage) — plus a holiday/event calendar to explain
  temporal variance.
- **Ensemble blend in production.** STGCN predictions are exported; blending them
  with the LightGBM latent rate into the live priority score is a small wiring step.

## Data sources
- Violations CSV — organizer-provided (gitignored, not committed).
- Road network & POIs — OpenStreetMap via OSMnx/Overpass (free).
- BBMP ward boundaries — public DataMeet / Open City GeoJSON (`backend/BBMP.geojson`).
- Live traffic (optional map layer) — Mappls Web Maps SDK.
