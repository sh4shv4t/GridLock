"""
Gridlock CIS API — thin, read-only service over precomputed Congestion Impact
Scores. Serves the exact per-hotspot contract produced by the model pipeline
(model/gridlock_pipeline.py -> cis_hotspots.json).

It does NOT recompute the full score on each request (the heavy scoring lives in
the batch pipeline). It optionally refreshes only the ECS component live from
Mappls when a token is configured — with last-known caching + low_confidence
fallback exactly as the CIS spec requires.

Run:
    pip install -r requirements.txt
    export GRIDLOCK_DATA=../frontend/public/cis_hotspots.json   # optional
    export MAPPLS_TOKEN=...                                      # optional (enables live=true)
    uvicorn main:app --reload --port 8000
Docs at http://localhost:8000/docs
"""
import json, os, sys, time
from datetime import datetime, timezone
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "model"))
from baseline_store import BaselineStore  # rolling 8-week ECS baseline
import db as cis_db                        # optional SQLite-backed serving

# If GRIDLOCK_DB points at a populated SQLite file, serve from it; else from JSON.
DB_PATH = os.environ.get("GRIDLOCK_DB", "")
def _db_mode():
    return bool(DB_PATH) and os.path.exists(DB_PATH)

DATA_CANDIDATES = [
    os.environ.get("GRIDLOCK_DATA", ""),
    "../frontend/public/cis_hotspots.json",
    "../model/outputs/cis_hotspots.json",
    "cis_hotspots.json",
]
MAPPLS_TOKEN = os.environ.get("MAPPLS_TOKEN", "")
ECS_WEIGHT = 0.35          # must match CIS_WEIGHTS["ECS"] in the pipeline
CACHE_TTL = 300            # seconds to trust a live ECS value

# Rolling 8-week baseline (per segment, per hour-of-week). Read on every live
# refresh; populated separately by a clean-segment poller (hotspot queries are
# violation windows and must NOT feed it). Starts empty -> baseline 0 until ready.
_baseline = BaselineStore(os.environ.get("GRIDLOCK_BASELINE", "ecs_baseline.json"))

def _hour_of_week(dt=None):
    dt = dt or datetime.now(timezone.utc).astimezone()
    return dt.weekday() * 24 + dt.hour

app = FastAPI(title="Gridlock CIS API", version="1.0",
              description="Explainable Congestion Impact Scores for parking hotspots.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_state = {"path": None, "mtime": 0.0, "records": [], "by_id": {}}
_ecs_cache = {}            # hotspot_id -> (ecs, ts)


def _resolve_path() -> str:
    for p in DATA_CANDIDATES:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("cis_hotspots.json not found. Run the model pipeline or set GRIDLOCK_DATA.")


def _load(force=False):
    """Hot-reload the JSON if the file changed on disk."""
    path = _resolve_path()
    mtime = os.path.getmtime(path)
    if force or path != _state["path"] or mtime != _state["mtime"]:
        records = json.load(open(path, encoding="utf-8"))
        _state.update(path=path, mtime=mtime, records=records,
                      by_id={r["hotspot_id"]: r for r in records})
    return _state["records"]


def _mappls_flow(lat, lng):
    """Best-effort live speed lookup. Returns (current_speed, free_flow_speed) or None.
    Endpoint/params are provider-specific; kept defensive so a failure -> fallback."""
    if not MAPPLS_TOKEN:
        return None
    try:
        r = requests.get(
            "https://apis.mappls.com/advancedmaps/v1/{}/traffic_flow".format(MAPPLS_TOKEN),
            params={"lat": lat, "lng": lng}, timeout=8)
        r.raise_for_status()
        d = r.json()
        cur = d.get("current_speed") or d.get("speed")
        free = d.get("free_flow_speed") or d.get("freeFlowSpeed")
        if cur is not None and free:
            return float(cur), float(free)
    except Exception:
        return None
    return None


def _refresh_ecs(rec):
    """Recompute ONLY the ECS component live; reuse cache on timeout (low_confidence)."""
    hid = rec["hotspot_id"]
    now = time.time()
    cached = _ecs_cache.get(hid)
    flow = _mappls_flow(rec["lat"], rec["lng"])
    if flow:
        cur, free = flow
        live_ratio = 1 - (cur / free) if free else 0.0
        baseline = _baseline.get(hid, _hour_of_week())     # rolling 8-week clean baseline
        ecs = min(max(0.0, live_ratio - baseline) / 0.5, 1.0)
        _ecs_cache[hid] = (ecs, now)
        low_conf = not _baseline.ready(hid, _hour_of_week())  # low-conf until baseline warms up
        reason = (f"live speed {cur:.0f} km/h vs free-flow {free:.0f} km/h"
                  + ("" if not low_conf else ", baseline warming up"))
    elif cached and now - cached[1] < CACHE_TTL:
        ecs, low_conf, reason = cached[0], True, "cached live value (provider timeout)"
    else:
        return rec  # no live data available -> return stored (proxy) record unchanged

    # rebuild the contract with the new ECS, holding the other components fixed
    out = json.loads(json.dumps(rec))
    out["components"]["excess_congestion"] = round(100 * ECS_WEIGHT * ecs, 1)
    out["cis"] = round(sum(out["components"].values()), 1)
    out["class"] = ("Critical" if out["cis"] >= 75 else "High" if out["cis"] >= 50
                    else "Medium" if out["cis"] >= 25 else "Low")
    out["low_confidence"] = low_conf
    out["confidence"] = 0.9 if not low_conf else 0.65
    out["explanation"] = [e for e in out["explanation"] if not e.startswith("Excess congestion")]
    out["explanation"].insert(0, f"Excess congestion +{out['components']['excess_congestion']:.1f} pts ({reason})")
    out["explanation"].sort(key=lambda s: float(s.split('+')[1].split(' ')[0]), reverse=True)
    return out


@app.get("/health")
def health():
    src = f"sqlite:{DB_PATH}" if _db_mode() else (_load() and _state["path"])
    n = len(cis_db.query_top(DB_PATH, limit=100000)) if _db_mode() else len(_state["records"])
    return {"status": "ok", "records": n, "source": src,
            "backend": "sqlite" if _db_mode() else "json",
            "live_ecs_enabled": bool(MAPPLS_TOKEN)}


@app.get("/hotspots")
def list_hotspots(
    cls: Optional[str] = Query(None, alias="class", description="Filter by class"),
    min_cis: float = Query(0.0, ge=0, le=100),
    limit: int = Query(100, ge=1, le=2000),
):
    if _db_mode():
        out = cis_db.query_top(DB_PATH, limit=limit, cls=cls, min_cis=min_cis)
        return {"count": len(out), "results": out}
    recs = _load()
    out = [r for r in recs if r["cis"] >= min_cis and (cls is None or r["class"].lower() == cls.lower())]
    return {"count": len(out), "results": out[:limit]}


@app.get("/hotspots/{hotspot_id}")
def get_hotspot(hotspot_id: str, live: bool = Query(False, description="Refresh ECS from Mappls if a token is set")):
    if _db_mode():
        rec = cis_db.get_by_id(hotspot_id, DB_PATH)
    else:
        _load()
        rec = _state["by_id"].get(hotspot_id)
    if not rec:
        raise HTTPException(404, f"hotspot_id {hotspot_id} not found")
    return _refresh_ecs(rec) if live else rec


@app.post("/reload")
def reload_data():
    return {"reloaded": len(_load(force=True))}
