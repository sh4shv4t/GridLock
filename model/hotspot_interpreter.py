"""
Hotspot interpreter — the per-location "why this corner".

Where socioeconomic_insights.py answers the *aggregate* "why" (which factors
correlate with parking pressure across all hotspots), this module answers it
**one hotspot at a time**: for each high-impact cell it assembles a structured
evidence profile and turns it into a plain-language hypothesis for why that exact
spot is a traffic hotspot.

Evidence assembled per hotspot (all free, no extra data collection):
  • Place character  -> named OSM POIs nearby (temple / mall / market / school /
                        hospital / metro / office) + a dominant "primary type".
  • Infrastructure   -> OSM road class, lanes, surface, street-lighting, and the
                        demand-vs-capacity ratio (is the road under-provisioned
                        for the activity around it?).
  • Income proxy     -> commercial-POI density percentile (+ VIIRS radiance if a
                        raster was supplied via viirs_equity.py). There is no
                        per-capita income in the data, so this is flagged a proxy.

The reason text is synthesised by **Gemini** (set GEMINI_API_KEY) grounded ONLY
in that evidence. With no key — or if the call fails — it falls back to a
deterministic rule-based explanation, so the module runs free and offline like
the rest of the pipeline (same swappable pattern as ECSProvider in
gridlock_pipeline.py).

Inputs : cis_features.csv + cis_hotspots.json (from gridlock_pipeline.py),
         optional equity.json (for ward radiance, from viirs_equity.py).
Output : hotspot_interpretations.json  (written to frontend/public + outputs).

Run:
    pip install -r requirements.txt           # no new deps; urllib is stdlib
    export GEMINI_API_KEY=...                  # optional — enables LLM reasons
    python hotspot_interpreter.py [--limit 50] [--no-llm] [--model gemini-2.0-flash]
"""
import argparse, json, os, time, urllib.request, urllib.error
import numpy as np
import pandas as pd

OSM_ENABLED = os.environ.get("GRIDLOCK_OSM", "1") == "1"
CONTEXT_RADIUS_M = 300          # how far around a hotspot counts as "nearby"
BENGALURU_UTM = 32643           # EPSG:32643 (UTM 43N) — metric CRS for buffers

# OSM tag -> our place category. First match wins in CATEGORY_ORDER.
CATEGORY_ORDER = ["religious", "retail", "market", "transit", "education",
                  "healthcare", "office", "food"]
CATEGORY_LABEL = {
    "religious": "place of worship", "retail": "shopping/mall", "market": "market",
    "transit": "transit hub", "education": "school/college", "healthcare": "hospital/clinic",
    "office": "office/commercial", "food": "restaurants/cafes",
}


# ── data loading ──────────────────────────────────────────────────────────────
def _find(name):
    for d in ["outputs", "../frontend/public", "../model/outputs", "."]:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(name + " not found — run gridlock_pipeline.py first.")


def _find_opt(name):
    try:
        return _find(name)
    except FileNotFoundError:
        return None


def load_hotspots():
    """Join per-cell features with the CIS contract on the H3 id."""
    feat = pd.read_csv(_find("cis_features.csv"))   # carries cis, cis_class, latent_rate, POI counts
    hs = pd.DataFrame(json.load(open(_find("cis_hotspots.json"), encoding="utf-8")))
    hs = hs.rename(columns={"hotspot_id": "h3"})[["h3", "ward_name", "lat", "lng"]]
    df = feat.merge(hs, on="h3", how="left")
    # demand-vs-capacity (same formula as the pipeline; not stored in the CSV)
    cap = (df["road_throughput"].replace(0, np.nan) / 400)
    df["demand_supply"] = ((df["poi_commercial"] + df["poi_transit"]) / cap).fillna(0).round(2)
    return df.dropna(subset=["lat", "lng"])


def ward_radiance():
    """ward_name -> mean VIIRS radiance, if viirs_equity.py has been run with a raster."""
    p = _find_opt("equity.json")
    if not p:
        return {}
    rep = json.load(open(p, encoding="utf-8"))
    if not rep.get("radiance_available"):
        return {}
    out = {}
    for w in rep.get("top_enforced_wards", []) + rep.get("most_under_enforced_low_activity", []):
        if w.get("radiance") is not None:
            out[w["ward_name"]] = w["radiance"]
    return out


# ── OSM place + infrastructure context (one bbox fetch, assigned per hotspot) ───
def _first(v):
    return v[0] if isinstance(v, list) else v


def osm_context(df, radius_m=CONTEXT_RADIUS_M, enabled=OSM_ENABLED):
    """Return {h3: {landmarks, category_counts, primary_type, surface, lit}}.

    One features+roads fetch over the hotspot bbox, then a metric-buffer spatial
    join per hotspot. Degrades to {} (caller falls back to aggregate counts) on
    any failure, so the module never hard-depends on a live Overpass."""
    if not enabled or df.empty:
        return {}
    try:
        import geopandas as gpd, osmnx as ox
        from shapely.geometry import Point

        lats, lngs = df["lat"].tolist(), df["lng"].tolist()
        bbox = (min(lngs) - 0.01, min(lats) - 0.01, max(lngs) + 0.01, max(lats) + 0.01)

        tags = {"shop": True, "office": True, "public_transport": True, "railway": ["station"],
                "amenity": ["place_of_worship", "marketplace", "school", "college", "university",
                            "hospital", "clinic", "bus_station", "restaurant", "cafe", "bar", "bank"]}
        feats = ox.features_from_bbox(bbox, tags=tags)
        feats = feats[feats.geometry.notna()].copy()
        feats["geometry"] = feats.geometry.representative_point()
        feats = gpd.GeoDataFrame(feats, geometry="geometry", crs=4326).to_crs(BENGALURU_UTM)

        def col(name):
            return feats[name] if name in feats.columns else pd.Series(index=feats.index, dtype=object)

        amen, shop = col("amenity"), col("shop")
        cat = pd.Series("other", index=feats.index)
        cat[col("office").notna() | amen.eq("bank")] = "office"
        cat[amen.isin(["restaurant", "cafe", "bar"])] = "food"
        cat[amen.isin(["hospital", "clinic"])] = "healthcare"
        cat[amen.isin(["school", "college", "university"])] = "education"
        cat[col("railway").eq("station") | amen.eq("bus_station") | col("public_transport").notna()] = "transit"
        cat[amen.eq("marketplace") | shop.eq("mall")] = "market"
        cat[shop.notna()] = "retail"
        cat[shop.eq("mall")] = "retail"
        cat[amen.eq("marketplace")] = "market"
        cat[amen.eq("place_of_worship")] = "religious"
        feats["cat"] = cat
        feats["name"] = col("name")

        # roads: surface + street-lighting as an infra-maintenance signal
        roads = None
        try:
            G = ox.graph_from_bbox(bbox, network_type="drive", simplify=True)
            roads = ox.graph_to_gdfs(G, nodes=False)[["surface", "lit", "geometry"]].to_crs(BENGALURU_UTM)
        except Exception:
            roads = None

        pts = gpd.GeoDataFrame(df[["h3"]].copy(),
                               geometry=[Point(x, y) for x, y in zip(df["lng"], df["lat"])], crs=4326)
        pts = pts.to_crs(BENGALURU_UTM)
        pts["buf"] = pts.geometry.buffer(radius_m)
        buf = pts.set_geometry("buf")[["h3", "buf"]]

        ctx = {}
        joined = gpd.sjoin(feats, buf, how="inner", predicate="within")
        for h3id, grp in joined.groupby("h3"):
            counts = grp[grp["cat"] != "other"]["cat"].value_counts().to_dict()
            primary = next((c for c in CATEGORY_ORDER if counts.get(c)), None)
            # named landmarks — prefer the "anchor" categories people recognise
            anchors = grp[grp["cat"].isin(["religious", "retail", "market", "transit", "healthcare", "education"])]
            names = [n for n in anchors["name"].dropna().unique().tolist() if isinstance(n, str)][:4]
            ctx.setdefault(h3id, {})["category_counts"] = {k: int(v) for k, v in counts.items()}
            ctx[h3id]["primary_type"] = primary
            ctx[h3id]["landmarks"] = names

        if roads is not None and len(roads):
            rj = gpd.sjoin(roads, buf, how="inner", predicate="intersects")
            for h3id, grp in rj.groupby("h3"):
                surf = grp["surface"].dropna().map(_first)
                lit = grp["lit"].dropna().map(_first)
                ctx.setdefault(h3id, {})
                ctx[h3id]["surface"] = (surf.mode().iloc[0] if len(surf) else None)
                ctx[h3id]["lit"] = (str(lit.mode().iloc[0]).lower() if len(lit) else None)
        return ctx
    except Exception as e:
        print("  [osm context skipped — falling back to aggregate counts]", e)
        return {}


# ── derived proxies ─────────────────────────────────────────────────────────
def _band(series):
    """Percentile -> low / moderate / high across the hotspot set."""
    pct = series.rank(pct=True)
    return pct.map(lambda p: "high" if p >= 0.66 else "moderate" if p >= 0.33 else "low")


def income_band(df, rad_map):
    """Income proxy band per hotspot. Commercial density, blended with VIIRS
    radiance when a raster was supplied (it's the only direct income signal)."""
    comm = _band(df["poi_commercial"])
    if rad_map:
        rad = df["ward_name"].map(rad_map)
        if rad.notna().any():
            return _band(rad.fillna(rad.median())), True
    return comm, False


def infra_assessment(row, ctx):
    """Plain-language infra read: is capacity under-provisioned for the demand,
    and are there visible under-maintenance signals (unpaved / unlit)?"""
    notes, flags = [], []
    rank = int(row.get("road_class_rank", 0)) if "road_class_rank" in row else None
    lanes = float(row.get("n_lanes", 0) or 0)
    ds = float(row.get("demand_supply", 0) or 0)
    if ds >= 3:
        flags.append("under_provisioned")
        notes.append(f"high activity on a road of limited capacity (demand/supply ≈ {ds:.1f})")
    if lanes and lanes <= 2:
        notes.append(f"narrow carriageway (~{lanes:.0f} lanes)")
    surf = (ctx or {}).get("surface")
    lit = (ctx or {}).get("lit")
    if surf and surf not in ("asphalt", "paved", "concrete"):
        flags.append("poor_surface"); notes.append(f"road surface tagged '{surf}'")
    if lit == "no":
        flags.append("unlit"); notes.append("no street lighting mapped")
    level = "low" if len(flags) >= 2 else "moderate" if flags else "adequate"
    return {"investment_level": level, "flags": flags, "notes": notes,
            "demand_supply": round(ds, 2), "n_lanes": round(lanes, 1)}


def build_evidence(row, ctx, inc_band, inc_is_radiance):
    ctx = ctx or {}
    cats = ctx.get("category_counts") or {}
    if not cats:  # OSM context unavailable -> derive a coarse type from aggregate counts
        agg = {"retail": row.get("poi_commercial", 0), "transit": row.get("poi_transit", 0),
               "education": row.get("poi_institutional", 0)}
        cats = {k: int(v) for k, v in agg.items() if v}
        primary = max(cats, key=cats.get) if cats else None
    else:
        primary = ctx.get("primary_type")
    return {
        "hotspot_id": str(row["h3"]),
        "lat": round(float(row["lat"]), 6), "lng": round(float(row["lng"]), 6),
        "ward_name": str(row.get("ward_name", "")),
        "cis": round(float(row["cis"]), 1), "cis_class": str(row.get("cis_class", "")),
        "latent_rate": round(float(row.get("latent_rate", 0)), 3),
        "place": {
            "primary_type": primary,
            "primary_label": CATEGORY_LABEL.get(primary, primary or "mixed use"),
            "landmarks": ctx.get("landmarks", []),
            "category_counts": cats,
        },
        "infrastructure": infra_assessment(row, ctx),
        "income_proxy": {
            "level": inc_band,
            "basis": "VIIRS night-light radiance (ward)" if inc_is_radiance else "commercial-POI density",
            "commercial_poi": int(row.get("poi_commercial", 0)),
            "note": "proxy — dataset has no per-capita / per-sq-ft income",
        },
    }


# ── reason synthesis ──────────────────────────────────────────────────────────
def rule_based_reason(ev):
    """Deterministic fallback explanation built from the evidence dict."""
    place, infra, inc = ev["place"], ev["infrastructure"], ev["income_proxy"]
    factors = []
    lm = ", ".join(place["landmarks"][:3])
    if place["primary_type"]:
        anchor = f"a {place['primary_label']}" + (f" ({lm})" if lm else "")
        factors.append(f"draws concentrated demand from {anchor} nearby")
    if "under_provisioned" in infra["flags"]:
        factors.append("the surrounding road capacity is under-provisioned for that demand")
    for n in infra["notes"]:
        if "lanes" in n or "surface" in n or "lighting" in n:
            factors.append(n)
    if inc["level"] == "high":
        factors.append("high commercial intensity (a high-activity, likely higher-rent locality)")
    elif inc["level"] == "low":
        factors.append("lower mapped economic activity — limited formal off-street parking")
    if not factors:
        factors = ["concentrated, recurring parking demand exceeding kerbside supply"]
    primary = factors[0]
    reason = (f"This spot scores CIS {ev['cis']} ({ev['cis_class']}) likely because it "
              f"{primary}" + (f", and {factors[1]}" if len(factors) > 1 else "") + ". "
              "Illegal parking accumulates where demand outstrips orderly supply, "
              "spilling vehicles onto the carriageway and choking throughput.")
    return {"primary_driver": place["primary_label"] if place["primary_type"] else "demand-supply gap",
            "reason": reason, "factors": factors, "source": "rule-based"}


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

GEMINI_PROMPT = """You are a transport-planning analyst. Using ONLY the evidence \
below, give a concise, plausible explanation for why this location is an \
illegal-parking / traffic hotspot in Bengaluru. Do not invent facts, statistics, \
or place names that are not in the evidence. If a landmark name is given, you may \
reference it. Connect the place character, infrastructure, and income proxy into a \
short causal story.

Return STRICT JSON only, no markdown, with keys:
  "primary_driver": a 2-5 word phrase naming the single biggest cause,
  "reason": 2-3 sentences of plain English,
  "factors": array of 2-4 short evidence-grounded bullet strings.

Evidence:
{evidence}
"""


class GeminiInterpreter:
    """Swappable LLM reasoner. Falls back to rule_based_reason on no key / error,
    mirroring the ECSProvider pattern in gridlock_pipeline.py."""

    def __init__(self, model="gemini-2.0-flash", api_key=None, enabled=True):
        self.model = model
        self.key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.enabled = enabled and bool(self.key)
        self.calls = self.failures = 0

    def available(self):
        return self.enabled

    def _call(self, evidence):
        body = {
            "contents": [{"parts": [{"text": GEMINI_PROMPT.format(
                evidence=json.dumps(evidence, ensure_ascii=False))}]}],
            "generationConfig": {"temperature": 0.4, "response_mime_type": "application/json"},
        }
        url = GEMINI_URL.format(model=self.model, key=self.key)
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        text = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(text)

    def reason(self, ev):
        if not self.available():
            return rule_based_reason(ev)
        self.calls += 1
        try:
            out = self._call(ev)
            return {"primary_driver": str(out.get("primary_driver", ""))[:80],
                    "reason": str(out.get("reason", "")).strip(),
                    "factors": [str(f) for f in (out.get("factors") or [])][:4],
                    "source": self.model}
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, ValueError, TimeoutError) as e:
            self.failures += 1
            if self.failures <= 3:
                print(f"  [gemini fallback for {ev['hotspot_id']}: {e}]")
            return rule_based_reason(ev)


# ── orchestration ─────────────────────────────────────────────────────────────
def interpret(limit=50, use_llm=True, model="gemini-2.0-flash"):
    df = load_hotspots().sort_values("cis", ascending=False).head(limit).reset_index(drop=True)
    rad = ward_radiance()
    inc_bands, inc_is_radiance = income_band(df, rad)
    df["_inc"] = inc_bands.values
    ctx = osm_context(df)
    llm = GeminiInterpreter(model=model, enabled=use_llm)
    mode = f"Gemini ({model})" if llm.available() else "rule-based (no GEMINI_API_KEY)"
    print(f"Interpreting {len(df)} hotspots | reasoning: {mode} | OSM context: "
          f"{'on' if ctx else 'off/fallback'}")

    out = []
    for _, row in df.iterrows():
        ev = build_evidence(row, ctx.get(row["h3"]), row["_inc"], inc_is_radiance)
        r = llm.reason(ev)
        ev.update(reason=r["reason"], primary_driver=r["primary_driver"],
                  factors=r["factors"], reason_source=r["source"])
        out.append(ev)
        if llm.available():
            time.sleep(0.5)  # gentle on the free-tier rate limit

    for d in ["../frontend/public", "outputs"]:
        if os.path.isdir(d):
            json.dump(out, open(os.path.join(d, "hotspot_interpretations.json"), "w",
                                encoding="utf-8"), indent=2, ensure_ascii=False)
    n_llm = sum(1 for o in out if o["reason_source"] != "rule-based")
    print(f"Wrote hotspot_interpretations.json | {len(out)} hotspots "
          f"({n_llm} LLM-reasoned, {len(out) - n_llm} rule-based)")
    if out:
        e = out[0]
        print(f"\nExample — {e['ward_name']} (CIS {e['cis']}, driver: {e['primary_driver']}):")
        print("  " + e["reason"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Per-hotspot interpretability (place / infra / income -> reason).")
    ap.add_argument("--limit", type=int, default=50, help="how many top-CIS hotspots to interpret")
    ap.add_argument("--no-llm", action="store_true", help="force the rule-based reasoner")
    ap.add_argument("--model", default="gemini-2.0-flash", help="Gemini model id")
    args = ap.parse_args()
    interpret(limit=args.limit, use_llm=not args.no_llm, model=args.model)


if __name__ == "__main__":
    main()
