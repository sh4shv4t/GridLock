"""
Socio-economic insight engine — the "why" behind the hotspots.

Hotspots aren't random: they cluster around economic activity, social
infrastructure, and road capacity. This module joins each hotspot's model
features with proxies for three socio-economic dimensions and quantifies how
each relates to illegal-parking pressure (latent rate) and congestion impact
(CIS) — producing plain-language insights, not just a map.

Dimensions (all from data we already pull — no extra collection):
  • Economic activity / income     -> commercial POI density (+ VIIRS radiance if supplied)
  • Proximity to social centres     -> institutional + transit POIs, metro within 500 m
  • Infrastructure (road capacity)  -> OSM road throughput, lane count

Joins cis_features.csv (per-cell features) with cis_hotspots.json (ward, lat/lng)
on the H3 id. Writes insights.json.  Run:  python socioeconomic_insights.py
"""
import json, os
import numpy as np
import pandas as pd

FACTORS = {
    "commercial_density": ("Economic activity", "poi_commercial"),
    "transit_proximity":  ("Social-centre proximity", "poi_transit"),
    "institutional_proximity": ("Social-centre proximity", "poi_institutional"),
    "metro_500m":         ("Social-centre proximity", "metro_500m"),
    "road_capacity":      ("Infrastructure", "road_throughput"),
    "lanes":              ("Infrastructure", "n_lanes"),
}


def _find(name):
    for d in ["outputs", "../frontend/public", "../model/outputs", "."]:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(name + " not found — run gridlock_pipeline.py first.")


def load_joined():
    feat = pd.read_csv(_find("cis_features.csv"))
    hs = pd.DataFrame(json.load(open(_find("cis_hotspots.json"), encoding="utf-8")))
    hs = hs.rename(columns={"hotspot_id": "h3"})[["h3", "ward_name", "lat", "lng"]]
    return feat.merge(hs, on="h3", how="left")


def correlations(df):
    out = {}
    for key, (dim, col) in FACTORS.items():
        if col not in df:
            continue
        out[key] = {
            "dimension": dim,
            "corr_with_latent": round(float(df[col].corr(df["latent_rate"])), 3),
            "corr_with_cis": round(float(df[col].corr(df["cis"])), 3),
        }
    return out


def contrast(df, col, label):
    """Mean CIS for cells WITH vs WITHOUT a factor present (>0)."""
    if col not in df:
        return None
    has = df[df[col] > 0]["cis"].mean()
    non = df[df[col] == 0]["cis"].mean()
    if np.isnan(has) or np.isnan(non) or non == 0:
        return None
    return {"factor": label, "cis_with": round(float(has), 1),
            "cis_without": round(float(non), 1), "ratio": round(float(has / non), 2)}


def ward_table(df):
    g = df.groupby("ward_name").agg(
        hotspots=("h3", "size"),
        mean_cis=("cis", "mean"),
        commercial=("poi_commercial", "mean"),
        social_centres=("poi_institutional", "mean"),
        transit=("poi_transit", "mean"),
        road_capacity=("road_throughput", "mean"),
    ).reset_index()
    g = g[g["ward_name"].notna()].round(2)
    return g.sort_values("mean_cis", ascending=False)


def narrative(corr, contrasts, wards):
    bullets = []
    # strongest driver of latent demand
    drivers = sorted(corr.items(), key=lambda kv: abs(kv[1]["corr_with_latent"]), reverse=True)
    if drivers:
        k, v = drivers[0]
        bullets.append(f"Strongest correlate of debiased parking pressure: **{k.replace('_',' ')}** "
                       f"({v['dimension']}), r={v['corr_with_latent']:+.2f} with latent rate.")
    for c in contrasts:
        if c and c["ratio"] >= 1.1:
            bullets.append(f"Hotspots near **{c['factor']}** average {c['ratio']}× the CIS of those without "
                           f"({c['cis_with']} vs {c['cis_without']}).")
    if len(wards):
        top = wards.iloc[0]
        bullets.append(f"Highest-impact ward **{top['ward_name']}** pairs high commercial density "
                       f"({top['commercial']:.1f} POIs/cell) with mean CIS {top['mean_cis']:.1f} — an "
                       f"economic-activity-driven hotspot cluster.")
    bullets.append("Income disparity: commercial-POI density is the current economic proxy. Supplying a "
                   "VIIRS night-light raster (viirs_equity.py --viirs) adds a direct radiance-based income "
                   "axis to test enforcement equity across wards.")
    return bullets


def main():
    df = load_joined()
    corr = correlations(df)
    contrasts = [
        contrast(df, "poi_transit", "transit hubs"),
        contrast(df, "poi_institutional", "schools/hospitals/worship"),
        contrast(df, "poi_commercial", "commercial centres"),
        contrast(df, "metro_500m", "a metro station (≤500 m)"),
    ]
    wards = ward_table(df)
    report = {
        "n_hotspots": int(len(df)),
        "dimensions": ["Economic activity (income proxy)", "Social-centre proximity", "Infrastructure"],
        "correlations": corr,
        "factor_contrasts": [c for c in contrasts if c],
        "top_wards_by_cis": wards.head(12).to_dict("records"),
        "insights": narrative(corr, contrasts, wards),
        "radiance_note": "Pass a VIIRS raster to viirs_equity.py to add income-radiance to this analysis.",
    }
    for d in ["../frontend/public", "outputs"]:
        if os.path.isdir(d):
            json.dump(report, open(os.path.join(d, "insights.json"), "w", encoding="utf-8"), indent=2)
    print(f"Wrote insights.json | {len(df)} hotspots")
    print("\nKey insights:")
    for b in report["insights"]:
        print("  •", b.replace("**", ""))
    print("\nFactor → latent-rate / CIS correlation:")
    for k, v in corr.items():
        print(f"  {k:24s} {v['corr_with_latent']:+.2f} / {v['corr_with_cis']:+.2f}  ({v['dimension']})")


if __name__ == "__main__":
    main()
