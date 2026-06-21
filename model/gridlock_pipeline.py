# %% [markdown]
# # Gridlock 2.0 — Parking Intelligence: Debiased Hotspot Model
#
# **What this notebook does**
#
# The raw dataset is a *patrol log*, not a *violation census* — we only see
# violations where officers actually drove. Naively ranking cells by violation
# count just rediscovers patrol routes. This notebook instead estimates the
# **latent illegal-parking rate** (what we'd see if enforcement were uniform),
# then scores each location's **congestion impact** from road geometry. The
# product is an enforcement-priority score that surfaces real hotspots *and*
# enforcement blind spots.
#
# Pipeline:
# 1. Load + clean (UTC→IST time, parse violation/offence JSON)
# 2. Quick EDA (the patrol-bias signal)
# 3. H3 spatial indexing + 3h time slots
# 4. **Debiasing**: inverse-probability weighting, fixed-junction anchor,
#    device-ID negative sampling
# 5. **OSM enrichment** (road class/lanes/oneway, POI demand, metro buffers) —
#    one bbox fetch, joined locally (free, no API key)
# 6. **Congestion impact** from OSM road capacity
# 7. **LightGBM** latent-rate model with spatio-temporal CV + SHAP
# 8. Citywide prediction → priority score + blind-spot detector
# 9. Recidivist-vehicle clustering
# 10. Export JSON for the React map
#
# Runs free end-to-end in Colab (no API keys). VIIRS night-light socioeconomic
# layer is intentionally deferred — see the README "Future work" section.

# %%
# ── Install (Colab) ──────────────────────────────────────────────────────────
# !pip install -q pandas numpy h3 lightgbm scikit-learn shap osmnx geopandas shapely matplotlib
import os, json, math, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

# %%
# ── CONFIG ───────────────────────────────────────────────────────────────────
# Local run reads ../jan...csv. In Colab, upload the CSV and set CSV_PATH to it
# (e.g. "/content/jan to may police violation_anonymized791b166.csv").
def _find_csv():
    for p in [
        os.environ.get("GRIDLOCK_CSV", ""),
        "../jan to may police violation_anonymized791b166.csv",
        "jan to may police violation_anonymized791b166.csv",
        "/content/jan to may police violation_anonymized791b166.csv",
    ]:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("Set GRIDLOCK_CSV or place the violation CSV next to this notebook.")

CSV_PATH    = _find_csv()
OUT_DIR     = os.environ.get("GRIDLOCK_OUT", "outputs")     # JSON written here
H3_RES      = 9            # ~174 m edge ≈ city-block resolution
SLOT_HOURS  = 3            # 8 time slots per day
SAMPLE_FRAC = float(os.environ.get("GRIDLOCK_SAMPLE", "1.0"))  # <1.0 for fast local test
OSM_ENABLED = os.environ.get("GRIDLOCK_OSM", "1") == "1"
HEATMAP_SAMPLE = 10000     # raw points for the frontend heatmap
TOP_N_HOTSPOTS = 150       # enriched hotspots exported to the map
BLINDSPOT_TOP_N = 20       # how many cells citywide get the (curated) blind-spot flag
os.makedirs(OUT_DIR, exist_ok=True)
print("CSV:", CSV_PATH, "| out:", OUT_DIR, "| sample:", SAMPLE_FRAC, "| OSM:", OSM_ENABLED)

# %% [markdown]
# ## 1. Load & clean
#
# The whole dataset is parking violations, so the "PARKING" filter is a no-op we
# keep for safety. Timestamps are UTC (`+00`); converting to `Asia/Kolkata` is
# essential — the famous "53% midnight–6 AM" figure is a UTC artifact. In IST the
# real pattern is a **morning enforcement shift** with an afternoon/evening blind
# spot (almost no tickets 3 PM–10 PM IST).

# %%
def load_clean(csv_path, sample_frac=1.0):
    df = pd.read_csv(csv_path, low_memory=False)

    def is_parking(x):
        try:
            return any("PARKING" in v.upper() for v in json.loads(x))
        except Exception:
            return False
    df = df[df["violation_type"].apply(is_parking)].copy()
    df = df.dropna(subset=["latitude", "longitude"])

    # UTC -> IST
    ts = pd.to_datetime(df["created_datetime"], utc=True, errors="coerce")
    df = df[ts.notna()].copy()
    df["ts"]   = ts[ts.notna()].dt.tz_convert("Asia/Kolkata")
    df["date"] = df["ts"].dt.date.astype(str)
    df["hour"] = df["ts"].dt.hour.astype(int)
    df["dow"]  = df["ts"].dt.dayofweek.astype(int)          # 0=Mon
    df["slot"] = (df["hour"] // SLOT_HOURS).astype(int)     # 0..7

    # signals
    df["is_junction"] = df["junction_name"].fillna("").str.startswith("BTP")
    df["approved"]    = (df["validation_status"] == "approved")
    df["decided"]     = df["validation_status"].isin(["approved", "rejected"])
    df["n_viol"]      = df["violation_type"].apply(
        lambda x: len(json.loads(x)) if isinstance(x, str) and x.startswith("[") else 1)

    if sample_frac < 1.0:
        df = df.sample(frac=sample_frac, random_state=1).copy()
    return df

df = load_clean(CSV_PATH, SAMPLE_FRAC)
print(f"Rows: {len(df):,} | dates {df.date.min()}..{df.date.max()} | junctions {df.is_junction.mean():.1%}")

# %% [markdown]
# ## 2. EDA — the patrol-bias signal
# These plots are the narrative spine of the demo: the data describes *when and
# where police patrolled*, which is what we must debias.

# %%
def quick_eda(df):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 3, figsize=(16, 3.5))
    df["hour"].value_counts(normalize=True).sort_index().mul(100).plot(
        kind="bar", ax=ax[0], color="#1A237E")
    ax[0].set_title("Violations by hour (IST)\nmorning shift, evening blind spot")
    ax[0].set_xlabel("hour"); ax[0].set_ylabel("%")

    dr = df.groupby("police_station")["approved"].mean().sort_values()
    dr.tail(15).mul(100).plot(kind="barh", ax=ax[1], color="#00897B")
    ax[1].set_title("Approval rate by station (data-quality signal)")

    vc = df["vehicle_number"].value_counts()
    ax[2].hist(np.clip(vc.values, 0, 30), bins=30, color="#C62828")
    ax[2].set_title(f"Repeat offenders: {(vc>5).sum():,} vehicles >5 hits")
    ax[2].set_xlabel("violations per vehicle (clipped 30)")
    plt.tight_layout(); plt.show()

try:
    quick_eda(df)
except Exception as e:
    print("(EDA plot skipped:", e, ")")

# %% [markdown]
# ## 3. H3 spatial index + time slots
# Each violation gets an H3 cell (res 9). We also pre-compute ring-1 neighbours
# for spatial-lag features and for device-route negative sampling.

# %%
import h3

def add_h3(df, res=H3_RES):
    df = df.copy()
    df["h3"] = [h3.latlng_to_cell(la, lo, res)
                for la, lo in zip(df["latitude"], df["longitude"])]
    return df

df = add_h3(df)
print("Distinct H3 cells:", df["h3"].nunique())

def cell_centroid(c):
    la, lo = h3.cell_to_latlng(c)
    return la, lo

# %% [markdown]
# ## 4. Debiasing
#
# **(a) Inverse-probability weighting (IPW).** We can't know where police *didn't*
# go, but we can estimate how heavily each (station, hour) was patrolled from
# distinct active device-days, and up-weight violations seen under light patrol.
#
# **(b) Fixed-junction anchor.** BTP-coded records are camera-detected regardless
# of patrol — an unbiased subset. We trust them more (higher sample weight) and
# expose `is_junction` to the model.
#
# **(c) Device-ID negative sampling.** Group by (device_id, date) to reconstruct
# each officer's shift. Cells *adjacent* to where a device wrote tickets, in the
# same slot, with no violation, were almost certainly patrolled-and-clean → real
# negatives (y=0), distinct from "never observed" structural zeros.

# %%
def patrol_probability(df):
    """p_obs(station, hour) ∝ distinct active device-days. Returns IPW weight col."""
    g = (df.groupby(["police_station", "hour"])
           .apply(lambda x: x[["device_id", "date"]].drop_duplicates().shape[0])
           .rename("device_days").reset_index())
    g["p_obs"] = g["device_days"] / g["device_days"].max()
    g["p_obs"] = g["p_obs"].clip(lower=0.02)            # floor so weights stay finite
    g["ipw"]   = (1.0 / g["p_obs"]).clip(upper=20.0)
    return df.merge(g[["police_station", "hour", "p_obs", "ipw"]],
                    on=["police_station", "hour"], how="left")

df = patrol_probability(df)
df["ipw"]   = df["ipw"].fillna(df["ipw"].median())
df["p_obs"] = df["p_obs"].fillna(df["p_obs"].median())
# trust anchors more: junction records get a sample-weight bump
df["trust_w"] = np.where(df["is_junction"], 1.5, 1.0) * np.where(df["decided"] & ~df["approved"], 0.5, 1.0)
print("IPW range:", round(df.ipw.min(), 2), "..", round(df.ipw.max(), 2))

# %%
def synth_negatives(df, max_per_shift=6, seed=1):
    """Cells ring-1 adjacent to a device's ticketed cells (same date+slot) with
    no violation = patrolled-clean negatives."""
    rng = np.random.default_rng(seed)
    pos = set(zip(df["h3"], df["date"], df["slot"]))
    neg = {}
    for (dev, date, slot), grp in df.groupby(["device_id", "date", "slot"]):
        cells = grp["h3"].unique()
        cand = set()
        for c in cells:
            for nb in h3.grid_ring(c, 1):
                cand.add(nb)
        cand -= set(cells)
        cand = [c for c in cand if (c, date, slot) not in pos]
        if cand:
            pick = rng.choice(cand, size=min(max_per_shift, len(cand)), replace=False)
            for c in pick:
                neg[(c, date, slot)] = (dev,)
    rows = [{"h3": c, "date": d, "slot": s, "device_id": v[0],
             "y_raw": 0, "patrolled_clean": True} for (c, d, s), v in neg.items()]
    print(f"Synthesized {len(rows):,} patrolled-clean negatives")
    return pd.DataFrame(rows)

neg_df = synth_negatives(df)

# %% [markdown]
# ## 5. Build the modelling panel
# Rows = (cell, date, slot). Positives aggregate real violations + their debiased
# weights; negatives are the patrolled-clean cells. This keeps a real `date`
# axis so we can do honest forward-chaining temporal CV.

# %%
def build_panel(df, neg_df):
    df = df.copy()
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
    agg = (df.groupby(["h3", "date", "slot"])
             .agg(y_raw=("id", "size"),
                  y_ipw=("ipw", "sum"),
                  dow=("dow", "first"),
                  junction_share=("is_junction", "mean"),
                  approval_rate=("approved", "mean"),
                  trust_w=("trust_w", "mean"),
                  p_obs=("p_obs", "mean"))
             .reset_index())
    agg["patrolled_clean"] = False
    neg = neg_df.copy()
    neg["y_ipw"] = 0.0
    neg["dow"] = pd.to_datetime(neg["date"]).dt.dayofweek
    neg["junction_share"] = 0.0; neg["approval_rate"] = 0.0
    neg["trust_w"] = 0.5; neg["p_obs"] = agg["p_obs"].median()
    panel = pd.concat([agg, neg[agg.columns]], ignore_index=True)
    panel = panel.drop_duplicates(["h3", "date", "slot"], keep="first")
    panel["month"] = pd.to_datetime(panel["date"]).dt.to_period("M").astype(str)
    la_lo = panel["h3"].map(lambda c: cell_centroid(c))
    panel["lat"] = la_lo.map(lambda t: t[0]); panel["lng"] = la_lo.map(lambda t: t[1])
    return panel

panel = build_panel(df, neg_df)
print(f"Panel rows: {len(panel):,} | positives {(panel.y_raw>0).sum():,} | zeros {(panel.y_raw==0).sum():,}")

# %% [markdown]
# ## 6. OSM enrichment (one bbox fetch, joined locally — free)
# Road **supply** (class, lanes, oneway, signals) and parking **demand** (POIs,
# metro proximity). A violation on a 4-lane primary is a different congestion
# event than one on a residential lane — this is the model's strongest lever.

# %%
ROAD_THROUGHPUT = {  # veh/hr/lane proxy by OSM highway class
    "motorway": 2000, "trunk": 1800, "primary": 1500, "secondary": 1200,
    "tertiary": 900, "residential": 400, "unclassified": 400, "service": 200}
CLASS_RANK = {k: i for i, k in enumerate(
    ["service", "residential", "unclassified", "tertiary", "secondary",
     "primary", "trunk", "motorway"])}

def osm_features(cells, enabled=OSM_ENABLED):
    """Return DataFrame indexed by h3 cell with road + POI features."""
    base = pd.DataFrame({"h3": list(cells)})
    if not enabled:
        for c in ["road_class_rank", "road_throughput", "n_lanes", "is_oneway",
                  "n_signals", "poi_commercial", "poi_transit", "poi_institutional",
                  "metro_500m"]:
            base[c] = 0.0
        return base.set_index("h3")

    import geopandas as gpd, osmnx as ox
    from shapely.geometry import Polygon, Point
    lats = [cell_centroid(c)[0] for c in cells]; lngs = [cell_centroid(c)[1] for c in cells]
    north, south = max(lats) + 0.01, min(lats) - 0.01
    east, west   = max(lngs) + 0.01, min(lngs) - 0.01
    bbox = (west, south, east, north)

    # cell polygons
    polys = {c: Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in cells}
    cell_gdf = gpd.GeoDataFrame({"h3": list(polys)}, geometry=list(polys.values()), crs=4326)

    # roads
    try:
        G = ox.graph_from_bbox(bbox, network_type="drive", simplify=True)
        edges = ox.graph_to_gdfs(G, nodes=False)[["highway", "lanes", "oneway", "geometry"]]
        def first(v): return v[0] if isinstance(v, list) else v
        edges["hclass"] = edges["highway"].map(first).astype(str)
        edges["rank"]   = edges["hclass"].map(CLASS_RANK).fillna(1)
        edges["tput"]   = edges["hclass"].map(ROAD_THROUGHPUT).fillna(400)
        def to_lanes(v):
            try: return float(first(v))
            except Exception: return np.nan
        edges["lanes_n"] = edges["lanes"].map(to_lanes)
        edges["lanes_n"] = edges["lanes_n"].fillna(edges["lanes_n"].median() if edges["lanes_n"].notna().any() else 2)
        edges["oneway_b"] = edges["oneway"].map(lambda v: 1.0 if v is True or v == "yes" else 0.0)
        edges = edges.to_crs(4326)
        joined = gpd.sjoin(cell_gdf, edges, how="left", predicate="intersects")
        road = joined.groupby("h3").agg(
            road_class_rank=("rank", "max"), road_throughput=("tput", "max"),
            n_lanes=("lanes_n", "mean"), is_oneway=("oneway_b", "max")).fillna(0)
    except Exception as e:
        print("  [roads fallback]", e)
        road = pd.DataFrame(index=list(cells))
        for c in ["road_class_rank", "road_throughput", "n_lanes", "is_oneway"]:
            road[c] = 0.0

    # POIs + signals + metro
    try:
        tags = {"shop": True, "amenity": ["restaurant", "cafe", "bar", "hospital", "school",
                "bus_station", "place_of_worship"], "railway": ["station"],
                "public_transport": True, "highway": ["traffic_signals"]}
        feats = ox.features_from_bbox(bbox, tags=tags)
        feats = feats[feats.geometry.notna()].to_crs(4326)
        feats["rep"] = feats.geometry.representative_point()
        feats = feats.set_geometry("rep")
        j = gpd.sjoin(feats, cell_gdf, how="inner", predicate="within")
        def col(name):  # safe column access — missing tag -> all-False series
            return j[name] if name in j.columns else pd.Series(index=j.index, dtype=object)
        def cnt(mask): return j[mask].groupby("h3").size()
        poi = pd.DataFrame(index=list(cells))
        poi["poi_commercial"]    = cnt(col("shop").notna() | col("amenity").isin(["restaurant","cafe","bar"]))
        poi["poi_transit"]       = cnt(col("railway").eq("station") | col("amenity").eq("bus_station") | col("public_transport").notna())
        poi["poi_institutional"] = cnt(col("amenity").isin(["hospital","school","place_of_worship"]))
        sig = j[col("highway").eq("traffic_signals")].groupby("h3").size()
        poi["n_signals"] = sig
        # metro: railway=station with subway/metro tagging proxy -> transit count >0 near
        poi["metro_500m"] = (poi["poi_transit"].fillna(0) > 0).astype(float)
        poi = poi.fillna(0)
    except Exception as e:
        print("  [poi fallback]", e)
        poi = pd.DataFrame(index=list(cells))
        for c in ["poi_commercial","poi_transit","poi_institutional","n_signals","metro_500m"]:
            poi[c] = 0.0

    out = base.set_index("h3").join(road).join(poi).fillna(0)
    return out

osm = osm_features(panel["h3"].unique())
print("OSM features built for", len(osm), "cells; cols:", list(osm.columns))

# %% [markdown]
# ## 7. Spatial-lag features + final feature table
# Spatial autocorrelation is injected as features (ring-1/ring-2 neighbour
# violation means) instead of a graph layer — lighter and works great with trees.

# %%
def spatial_lags(panel):
    cell_rate = panel.groupby("h3")["y_raw"].mean()
    def ring_mean(c, k):
        nb = h3.grid_ring(c, k)
        vals = [cell_rate.get(n, 0.0) for n in nb]
        return float(np.mean(vals)) if vals else 0.0
    uniq = panel["h3"].unique()
    lag1 = {c: ring_mean(c, 1) for c in uniq}
    lag2 = {c: ring_mean(c, 2) for c in uniq}
    panel = panel.copy()
    panel["lag1_mean"] = panel["h3"].map(lag1)
    panel["lag2_mean"] = panel["h3"].map(lag2)
    return panel

panel = spatial_lags(panel)
panel = panel.join(osm, on="h3")
# demand/supply ratio: parking demand vs road capacity
panel["demand_supply"] = (panel["poi_commercial"] + panel["poi_transit"]) / (panel["road_throughput"].replace(0, np.nan) / 400)
panel["demand_supply"] = panel["demand_supply"].fillna(0)
print("Feature table shape:", panel.shape)

# %% [markdown]
# ## 8. LightGBM latent-rate model
# Target = `y_ipw` (debiased count). Tweedie objective handles the zero-inflated
# non-negative counts naturally. **Spatio-temporal CV**: forward-chain by month
# (train early → validate later) *and* hold out a geographic quadrant, so we test
# generalization to under-patrolled zones rather than re-learning patrol routes.

# %%
import lightgbm as lgb
from sklearn.metrics import mean_absolute_error

FEATURES = ["slot", "dow", "p_obs", "junction_share", "approval_rate",
            "lag1_mean", "lag2_mean", "road_class_rank", "road_throughput",
            "n_lanes", "is_oneway", "n_signals", "poi_commercial", "poi_transit",
            "poi_institutional", "metro_500m", "demand_supply", "lat", "lng"]
TARGET = "y_ipw"

def st_folds(panel):
    """Yield (train_idx, val_idx) for forward-chaining months × quadrant holdout."""
    months = sorted(panel["month"].unique())
    lat_med, lng_med = panel["lat"].median(), panel["lng"].median()
    quad = ((panel["lat"] > lat_med).astype(int) * 2 + (panel["lng"] > lng_med).astype(int))
    for i in range(1, len(months)):
        train_months = months[:i]; val_month = months[i]
        for q in panel.assign(q=quad)["q"].unique():
            tr = panel.index[panel["month"].isin(train_months) & (quad != q)]
            va = panel.index[(panel["month"] == val_month) & (quad == q)]
            if len(tr) > 100 and len(va) > 20:
                yield tr, va

PARAMS = dict(objective="tweedie", tweedie_variance_power=1.2,
              num_leaves=127, learning_rate=0.05, min_child_samples=50,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=5,
              lambda_l1=1.0, lambda_l2=1.0, verbose=-1,
              seed=42, bagging_seed=42, feature_fraction_seed=42)  # reproducible runs

def train_cv(panel):
    X = panel[FEATURES]; y = panel[TARGET]; w = panel["trust_w"]
    maes, models = [], []
    for k, (tr, va) in enumerate(st_folds(panel)):
        dtr = lgb.Dataset(X.loc[tr], y.loc[tr], weight=w.loc[tr])
        m = lgb.train(PARAMS, dtr, num_boost_round=300)
        pred = np.clip(m.predict(X.loc[va]), 0, None)
        mae = mean_absolute_error(y.loc[va], pred)
        maes.append(mae); models.append(m)
        if k < 8: print(f"  fold {k}: train {len(tr):,} val {len(va):,} MAE {mae:.3f}")
    print(f"CV MAE: {np.mean(maes):.3f} ± {np.std(maes):.3f}  ({len(maes)} folds)")
    final = lgb.train(PARAMS, lgb.Dataset(X, y, weight=w), num_boost_round=400)
    return final, maes

model, cv_maes = train_cv(panel)

# %%
# SHAP — what drives latent parking risk
try:
    import shap
    expl = shap.TreeExplainer(model)
    samp = panel[FEATURES].sample(min(2000, len(panel)), random_state=1)
    sv = expl.shap_values(samp)
    shap.summary_plot(sv, samp, show=True, max_display=15)
except Exception as e:
    print("(SHAP skipped:", e, ")")
    imp = pd.Series(model.feature_importance(), index=FEATURES).sort_values(ascending=False)
    print(imp.head(15).to_string())

# %% [markdown]
# ## 9. Citywide prediction → priority score + blind-spot detector
#
# Predict the **latent rate** per cell at a reference daytime slot. Then:
# * **priority_score** = normalized latent rate × road-criticality weight
#   (congestion impact, all OSM-derived — no traffic API needed).
# * **blind spot** = high predicted latent rate **and** high road criticality
#   **but** low observed patrol (`p_obs`). These are where impact is high and
#   enforcement hasn't reached — the money slide.

# %%
def predict_cells(panel, model):
    # one row per cell at a reference morning slot (slot=3 -> 09:00-12:00 IST), Tue
    cells = panel.drop_duplicates("h3").copy()
    cells["slot"] = 3; cells["dow"] = 1
    cells["latent_rate"] = np.clip(model.predict(cells[FEATURES]), 0, None)
    obs = panel.groupby("h3")["y_raw"].sum().rename("observed_count")
    pobs = panel.groupby("h3")["p_obs"].mean().rename("patrol_obs")
    cells = cells.merge(obs, on="h3").merge(pobs, on="h3")
    # congestion impact from road capacity (higher class + lanes = worse blockage)
    cap = (cells["road_throughput"] * np.maximum(cells["n_lanes"], 1))
    cells["road_weight"] = (cap / cap.max()).fillna(0) if cap.max() > 0 else 0.0
    lr = cells["latent_rate"]
    cells["latent_norm"] = (lr / lr.max()) if lr.max() > 0 else 0.0
    cells["priority_score"] = (cells["latent_norm"] * (0.5 + cells["road_weight"])).round(4)
    # Blind spot = high predicted impact but low observed patrol. We rank by an
    # explicit blind-spot score and flag only the worst BLINDSPOT_TOP_N, so the
    # flag stays a curated, striking subset rather than half the map.
    patrol_norm = cells["patrol_obs"] / (cells["patrol_obs"].max() or 1)
    cells["blindspot_score"] = (cells["latent_norm"] * (0.3 + cells["road_weight"]) * (1 - patrol_norm)).round(4)
    n_flag = min(BLINDSPOT_TOP_N, len(cells))
    thresh = cells["blindspot_score"].nlargest(n_flag).min() if n_flag else 1e9
    cells["blindspot"] = (cells["blindspot_score"] >= thresh) & (cells["blindspot_score"] > 0)
    return cells.sort_values("priority_score", ascending=False)

cells = predict_cells(panel, model)
print("Top cells by priority:")
print(cells[["lat","lng","observed_count","latent_rate","road_weight","priority_score","blindspot"]].head(8).to_string(index=False))
print("Blind spots flagged:", int(cells["blindspot"].sum()))

# %% [markdown]
# ## 10. Recidivist-vehicle clustering
# 2,352 vehicles appear >5 times. Vehicles that repeatedly offend at the *same*
# place are structured problems (delivery fleets, auto stands) — a different
# enforcement lever than spatial hotspots.

# %%
def recidivists(df, min_hits=6, top=200):
    vc = df["vehicle_number"].value_counts()
    repeat = vc[vc >= min_hits].index
    sub = df[df["vehicle_number"].isin(repeat)]
    rows = []
    for v, g in sub.groupby("vehicle_number"):
        top_cell = g["h3"].value_counts().idxmax()
        share = g["h3"].value_counts().iloc[0] / len(g)
        la, lo = cell_centroid(top_cell)
        rows.append({"vehicle": v, "hits": int(len(g)), "vehicle_type": g["vehicle_type"].mode().iloc[0],
                     "top_lat": round(la, 5), "top_lng": round(lo, 5),
                     "concentration": round(float(share), 2),
                     "is_fleet_pattern": bool(share > 0.5)})
    cols = ["vehicle", "hits", "vehicle_type", "top_lat", "top_lng", "concentration", "is_fleet_pattern"]
    if not rows:
        print("No recidivists at this sample size (need full data).")
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows).sort_values("hits", ascending=False).head(top)
    print(f"{len(repeat):,} repeat vehicles; {out['is_fleet_pattern'].sum()} show fixed-location patterns")
    return out

recid = recidivists(df)

# %% [markdown]
# ## 11. Export JSON for the React map
# Schema is backward-compatible with the existing frontend, plus new fields
# (`latent_rate`, `priority_score`, `blindspot`) and two new files.

# %%
import subprocess
def assign_wards(cells):
    """Optional BBMP ward join if backend/BBMP.geojson is present."""
    for path in ["../backend/BBMP.geojson", "backend/BBMP.geojson", "BBMP.geojson"]:
        if os.path.exists(path):
            try:
                import geopandas as gpd
                from shapely.geometry import Point
                wards = gpd.read_file(path)
                name_col = "KGISWardName" if "KGISWardName" in wards.columns else wards.columns[0]
                no_col   = "KGISWardNo" if "KGISWardNo" in wards.columns else None
                pts = gpd.GeoDataFrame(cells, geometry=[Point(x, y) for x, y in zip(cells["lng"], cells["lat"])], crs=4326)
                j = gpd.sjoin(pts, wards[[c for c in [name_col, no_col, "geometry"] if c]], how="left", predicate="within")
                cells["ward_name"] = j[name_col].fillna("Outside BBMP").values
                cells["ward_no"]   = (j[no_col].astype(str).values if no_col else "")
                print("Wards assigned from", path)
                return cells
            except Exception as e:
                print("  [ward join skipped]", e)
    cells["ward_name"] = "Unknown"; cells["ward_no"] = ""
    return cells

cells = assign_wards(cells)

def _to_hotspot(r, rank):
    return {
        "lat": round(float(r["lat"]), 6), "lng": round(float(r["lng"]), 6),
        "priority_rank": int(rank + 1),
        "violation_count": int(r["observed_count"]),
        "latent_rate": round(float(r["latent_rate"]), 3),
        "priority_score": round(float(r["priority_score"]), 3),
        "congestion_ratio": round(float(1.0 + r["road_weight"]), 2),  # OSM-derived impact multiplier (1=baseline)
        "road_weight": round(float(r["road_weight"]), 3),
        "blindspot": bool(r["blindspot"]),
        "locality": str(r.get("ward_name", "")),
        "ward_name": str(r.get("ward_name", "")), "ward_no": str(r.get("ward_no", "")),
    }

def export(df, cells, recid, out_dir=OUT_DIR):
    # heatmap (raw points — shows the patrol bias honestly)
    samp = df.sample(min(HEATMAP_SAMPLE, len(df)), random_state=1)
    heat = [{"lat": round(la, 5), "lng": round(lo, 5)}
            for la, lo in zip(samp["latitude"], samp["longitude"])]
    json.dump(heat, open(f"{out_dir}/heatmap_points.json", "w"))

    # DEBIASED ranking (the model's answer)
    top = cells.head(TOP_N_HOTSPOTS).reset_index(drop=True)
    hotspots = [_to_hotspot(r, rank) for rank, r in top.iterrows()]
    json.dump(hotspots, open(f"{out_dir}/hotspots_full.json", "w"), indent=2)

    # NAIVE baseline: rank by raw observed count (the "patrol-route predictor").
    # The before/after toggle in the UI contrasts this with the debiased ranking.
    raw = cells.sort_values("observed_count", ascending=False).head(TOP_N_HOTSPOTS).reset_index(drop=True)
    raw_hotspots = [_to_hotspot(r, rank) for rank, r in raw.iterrows()]
    json.dump(raw_hotspots, open(f"{out_dir}/hotspots_raw.json", "w"), indent=2)

    blind = [h for h in hotspots if h["blindspot"]]
    json.dump(blind, open(f"{out_dir}/blindspots.json", "w"), indent=2)
    json.dump(recid.to_dict("records"), open(f"{out_dir}/recidivists.json", "w"), indent=2)

    # how different is the debiased map from the naive one? (Jaccard of cell sets)
    dset = {(h["lat"], h["lng"]) for h in hotspots}
    rset = {(h["lat"], h["lng"]) for h in raw_hotspots}
    overlap = len(dset & rset) / max(len(dset | rset), 1)

    # HEADLINE SUMMARY for the map banner
    vc = df["vehicle_number"].value_counts()
    cap_exposure = float((top["road_throughput"] * np.maximum(top["n_lanes"], 1)).sum())
    summary = {
        "total_violations": int(len(df)),
        "date_range": [str(df["date"].min()), str(df["date"].max())],
        "n_hotspots": len(hotspots),
        "n_wards": int(top["ward_name"].nunique()),
        "n_blindspots_total": int(cells["blindspot"].sum()),
        "n_blindspots_in_top": int(sum(h["blindspot"] for h in hotspots)),
        "n_recidivists": int((vc >= 6).sum()),
        "evening_gap_pct": round(float(df["hour"].between(15, 21).mean() * 100), 1),  # 3pm-10pm IST
        "morning_peak_hour": int(df["hour"].mode().iloc[0]),
        "capacity_exposure_vph": int(round(cap_exposure, -2)),  # veh/hr road capacity at priority hotspots
        "debiased_vs_naive_overlap": round(float(overlap), 2),
    }
    json.dump(summary, open(f"{out_dir}/summary.json", "w"), indent=2)
    print(f"Wrote {len(heat)} heat pts, {len(hotspots)} hotspots ({len(blind)} blind), "
          f"{len(raw_hotspots)} raw-baseline, {len(recid)} recidivists, summary -> {out_dir}/")
    print(f"  Debiased vs naive top-{TOP_N_HOTSPOTS} overlap: {overlap:.0%} "
          f"(lower = model surfaces different cells than raw counts)")

export(df, cells, recid)

# %% [markdown]
# ## Done
# Copy `outputs/*.json` into `frontend/public/`. The map reads them directly.
#
# **Deferred (see README → Future work):** VIIRS night-light socioeconomic layer,
# STGCN ensemble model, set-cover patrol routing.
