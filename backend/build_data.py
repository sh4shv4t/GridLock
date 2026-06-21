"""
Gridlock — Final Backend Data Builder
Reads your violation CSV and produces two files for the React app:
  - heatmap_points.json   (FREE: raw violation points for the heatmap, 0 API calls)
  - hotspots_full.json    (PAID: top-N hotspots enriched with snap/name/congestion/reachability)

It prints a COST ESTIMATE and asks you to confirm before spending any credit.

================  COST MODEL (per enriched hotspot)  ================
  reverse geocode .......... 1 call
  distance matrix (x2) ..... 2 calls   (baseline + live traffic = congestion ratio)
  snap to road (optional) .. 1 call
  reachability (optional) .. 2 calls   (off-peak + peak isopolygon)
  ---------------------------------------------------------------
  Heatmap = 0 calls (free).
====================================================================
"""

import requests, json, time, sys
from datetime import datetime

# ─────────────────── CONFIG — edit these ───────────────────
STATIC_KEY     = "YOUR_MAPPLS_TOKEN_HERE"
CSV_PATH       = "jan_to_may_police_violation_anonymized791b166.csv"  # same folder as this script

ENRICH_COUNT       = 100     # how many top hotspots to fully enrich (geocode + congestion)
HEATMAP_SAMPLE     = 10000   # raw points for the heatmap (FREE, no API). 3000 looks great.
DO_SNAP            = True   # snap enriched hotspots to exact roads (+1 call each)
REACHABILITY_TOP_N = 0    # reachability polygons for only the top N (+2 calls each)

# TIP: To test cost cheaply FIRST, set ENRICH_COUNT=5, REACHABILITY_TOP_N=3,
# run it, then check your wallet in the console before scaling up.
# ────────────────────────────────────────────────────────────


def load_and_rank():
    import pandas as pd, warnings
    warnings.filterwarnings("ignore")
    print("Reading CSV...")
    df = pd.read_csv(CSV_PATH, low_memory=False)

    def is_parking(x):
        try:
            return any("PARKING" in v.upper() for v in json.loads(x))
        except Exception:
            return False

    df = df[df["violation_type"].apply(is_parking)].copy()
    df = df.dropna(subset=["latitude", "longitude"])
    print(f"Parking violations: {len(df)}")

    # heatmap sample (free)
    sample = df.sample(min(HEATMAP_SAMPLE, len(df)), random_state=1)
    heat = [{"lat": round(r.latitude, 5), "lng": round(r.longitude, 5)} for r in sample.itertuples()]
    with open("heatmap_points.json", "w") as f:
        json.dump(heat, f)
    print(f"Saved heatmap_points.json ({len(heat)} points, 0 API calls)")

    # grid -> top N hotspots
    df["glat"] = (df["latitude"] / 0.0014).round() * 0.0014
    df["glon"] = (df["longitude"] / 0.0014).round() * 0.0014
    top = (df.groupby(["glat", "glon"]).size().reset_index(name="count")
             .sort_values("count", ascending=False).head(ENRICH_COUNT))
    return [{"glat": round(r.glat, 5), "glon": round(r.glon, 5), "count": int(r.count)}
            for r in top.itertuples()]


def cost_estimate(n):
    per = 1 + 2 + (1 if DO_SNAP else 0)         # geocode + 2 DM + optional snap
    base = per * n
    reach = 2 * min(REACHABILITY_TOP_N, n)
    return base + reach


# ── API calls (all with graceful fallback) ──
def snap(lat, lng):
    if not DO_SNAP:
        return lat, lng, None
    try:
        r = requests.post("https://route.mappls.com/routev2/movement/trace_route",
                          params={"access_token": STATIC_KEY},
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          data={"points": f"{lng},{lat};{lng+0.0001},{lat+0.0001}", "type": "break", "search_radius": 50},
                          timeout=15)
        r.raise_for_status()
        d = r.json()
        tp = d.get("tracepoints", [])
        conf = (d.get("matchings", [{}])[0] or {}).get("confidence")
        if tp and tp[0].get("location"):
            return tp[0]["location"][1], tp[0]["location"][0], conf
    except Exception as e:
        print(f"      [snap fallback] {e}")
    return lat, lng, None


def geocode(lat, lng):
    try:
        r = requests.get("https://search.mappls.com/search/address/rev-geocode",
                         params={"lat": lat, "lng": lng, "access_token": STATIC_KEY}, timeout=10)
        r.raise_for_status()
        res = r.json().get("results", [])
        if res:
            return res[0].get("formatted_address", ""), (res[0].get("locality") or res[0].get("subLocality") or res[0].get("village") or "")
    except Exception as e:
        print(f"      [geocode fallback] {e}")
    return "", "Unknown"


def congestion(lat, lng):
    lat2, lng2 = lat, lng + 0.009
    coords = f"{lng},{lat};{lng2},{lat2}"
    try:
        b = requests.get(f"https://route.mappls.com/route/dm/distance_matrix/driving/{coords}",
                         params={"access_token": STATIC_KEY}, timeout=15).json()["results"]["durations"][0][1]
        t = requests.get(f"https://route.mappls.com/route/dm/distance_matrix_eta/driving/{coords}",
                         params={"access_token": STATIC_KEY, "region": "ind"}, timeout=15).json()["results"]["durations"][0][1]
        if b and b > 0:
            return round(t / b, 2)
    except Exception as e:
        print(f"      [congestion fallback] {e}")
    return 1.0


def reachability(lat, lng, do_it):
    if not do_it:
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    url = "https://route.mappls.com/routev2/optimization/isopolygon"
    def fetch(dt, color):
        try:
            r = requests.get(url, params={
                "locations": f"{lat},{lng}", "rangeType": "time", "costing": "auto",
                "speedTypes": "predictive", "date_time": dt, "contours": f"15,{color}",
                "polygons": "true", "access_token": STATIC_KEY}, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"      [reach fallback] {e}")
            return None
    return {
        "offpeak": fetch(f"1,{today}T04:00", "00aa00"),  # green, big
        "peak":    fetch(f"1,{today}T18:30", "cc0000"),  # red, small
    }


def main():
    if STATIC_KEY == "YOUR_STATIC_KEY_HERE":
        print("ERROR: set STATIC_KEY first."); return

    hotspots = load_and_rank()
    n = len(hotspots)
    est = cost_estimate(n)
    print("\n" + "=" * 55)
    print(f"PLAN: enrich {n} hotspots")
    print(f"  snap: {DO_SNAP} | reachability for top {REACHABILITY_TOP_N}")
    print(f"  ESTIMATED API CALLS: ~{est}")
    print(f"  (heatmap already saved, cost those 0 calls)")
    print("=" * 55)
    ans = input("Proceed and spend credit? type yes: ").strip().lower()
    if ans != "yes":
        print("Aborted. heatmap_points.json is still saved (free)."); return

    out, maxc = [], max(h["count"] for h in hotspots)
    for i, h in enumerate(hotspots, 1):
        rlat, rlng, c = h["glat"], h["glon"], h["count"]
        print(f"\n[{i}/{n}] {rlat},{rlng} ({c})")
        lat, lng, conf = snap(rlat, rlng)
        addr, loc = geocode(lat, lng)
        ratio = congestion(lat, lng)
        reach = reachability(lat, lng, i <= REACHABILITY_TOP_N)
        print(f"    {loc} | congestion {ratio}x | reach {'yes' if reach else 'no'}")
        out.append({"lat": lat, "lng": lng, "raw_lat": rlat, "raw_lng": rlng,
                    "snapped": (abs(lat-rlat) > 1e-6 or abs(lng-rlng) > 1e-6),
                    "snap_confidence": conf, "violation_count": c, "address": addr,
                    "locality": loc, "congestion_ratio": ratio,
                    "impact_score": round((c/maxc)*ratio, 3), "reachability": reach})
        time.sleep(0.25)

    out.sort(key=lambda x: x["impact_score"], reverse=True)
    for rank, h in enumerate(out, 1):
        h["priority_rank"] = rank
    with open("hotspots_full.json", "w") as f:
        json.dump(out, f, indent=2)

    print("\n" + "=" * 55)
    print(f"Saved hotspots_full.json ({len(out)} enriched)")
    print("CHECK YOUR WALLET in the Mappls console now to see spend.")
    print("=" * 55)
    print("\nTOP 5 BY IMPACT:")
    for h in out[:5]:
        print(f"  #{h['priority_rank']} {h['locality'][:30]:30s} viol={h['violation_count']:5d} cong={h['congestion_ratio']}x impact={h['impact_score']}")


if __name__ == "__main__":
    main()