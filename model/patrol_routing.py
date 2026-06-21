"""
Shift-optimal patrol routing — turns the CIS hotspot map into an ordered
"plan my route" for each patrol unit.

Framed as max-coverage set cover: each candidate stop "covers" the CIS-weighted
demand of every hotspot within COVER_RADIUS_KM. We greedily pick stops that add
the most uncovered CIS (greedy gets >=63% of optimal for set cover), split them
across N_UNITS, then order each unit's stops with a nearest-neighbour route.

Reads cis_hotspots.json (from the pipeline) and writes patrol_route.json.
Run:  python patrol_routing.py            # uses ../frontend/public or ../model/outputs
"""
import json, math, os, argparse

COVER_RADIUS_KM = 0.4   # a stop addresses hotspots within this radius
N_UNITS         = 4     # patrol units on shift
MAX_STOPS_TOTAL = 24    # total stops across all units (budget)


def haversine_km(a, b):
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def greedy_cover(spots, n_stops, radius_km):
    """Pick n_stops maximizing covered CIS. Returns selected indices."""
    pts = [(s["lat"], s["lng"]) for s in spots]
    cis = [max(s.get("cis", 0.0), 0.0) for s in spots]
    # precompute coverage neighbourhoods
    covers = []
    for i in range(len(spots)):
        covers.append([j for j in range(len(spots)) if haversine_km(pts[i], pts[j]) <= radius_km])
    covered = set()
    chosen = []
    for _ in range(min(n_stops, len(spots))):
        best, best_gain = None, -1.0
        for i in range(len(spots)):
            if i in chosen:
                continue
            gain = sum(cis[j] for j in covers[i] if j not in covered)
            if gain > best_gain:
                best, best_gain = i, gain
        if best is None or best_gain <= 0:
            break
        chosen.append(best)
        covered.update(covers[best])
    return chosen, covered


def nn_route(spots, idxs, start_idx):
    """Order idxs as a nearest-neighbour route starting from start_idx."""
    pts = {i: (spots[i]["lat"], spots[i]["lng"]) for i in idxs}
    route, remaining, cur = [start_idx], set(idxs) - {start_idx}, start_idx
    dist = 0.0
    while remaining:
        nxt = min(remaining, key=lambda j: haversine_km(pts[cur], pts[j]))
        dist += haversine_km(pts[cur], pts[nxt])
        route.append(nxt); remaining.discard(nxt); cur = nxt
    return route, dist


def build_routes(spots, n_units=N_UNITS, max_stops=MAX_STOPS_TOTAL, radius_km=COVER_RADIUS_KM):
    spots = sorted(spots, key=lambda s: s.get("cis", 0), reverse=True)
    chosen, covered = greedy_cover(spots, max_stops, radius_km)
    total_cis = sum(max(s.get("cis", 0.0), 0.0) for s in spots)
    covered_cis = sum(max(spots[j].get("cis", 0.0), 0.0) for j in covered)

    # split chosen stops across units by round-robin on descending CIS (balances load)
    chosen_sorted = sorted(chosen, key=lambda i: spots[i].get("cis", 0), reverse=True)
    buckets = [[] for _ in range(n_units)]
    for k, i in enumerate(chosen_sorted):
        buckets[k % n_units].append(i)

    units = []
    for u, idxs in enumerate(buckets):
        if not idxs:
            continue
        start = max(idxs, key=lambda i: spots[i].get("cis", 0))  # start at the worst hotspot
        order, dist = nn_route(spots, idxs, start)
        stops = [{
            "seq": seq + 1, "hotspot_id": spots[i]["hotspot_id"],
            "lat": spots[i]["lat"], "lng": spots[i]["lng"],
            "cis": spots[i].get("cis"), "class": spots[i].get("class"),
            "ward_name": spots[i].get("ward_name", ""),
        } for seq, i in enumerate(order)]
        units.append({"unit_id": u + 1, "n_stops": len(stops),
                      "route_km": round(dist, 2), "stops": stops})

    return {
        "n_units": len(units), "total_stops": len(chosen),
        "cover_radius_km": radius_km,
        "coverage_pct": round(100 * covered_cis / total_cis, 1) if total_cis else 0.0,
        "hotspots_covered": len(covered), "hotspots_total": len(spots),
        "units": units,
    }


def _resolve(name, write=False):
    for d in ["../frontend/public", "../model/outputs", "outputs", "."]:
        p = os.path.join(d, name)
        if write and os.path.isdir(d):
            return p
        if os.path.exists(p):
            return p
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", type=int, default=N_UNITS)
    ap.add_argument("--max-stops", type=int, default=MAX_STOPS_TOTAL)
    ap.add_argument("--radius-km", type=float, default=COVER_RADIUS_KM)
    args = ap.parse_args()

    src = _resolve("cis_hotspots.json")
    spots = json.load(open(src, encoding="utf-8"))
    result = build_routes(spots, args.units, args.max_stops, args.radius_km)
    out = _resolve("patrol_route.json", write=True)
    json.dump(result, open(out, "w", encoding="utf-8"), indent=2)
    print(f"Routed {result['total_stops']} stops across {result['n_units']} units "
          f"covering {result['coverage_pct']}% of CIS demand "
          f"({result['hotspots_covered']}/{result['hotspots_total']} hotspots) -> {out}")
    for u in result["units"]:
        print(f"  Unit {u['unit_id']}: {u['n_stops']} stops, {u['route_km']} km, "
              f"start {u['stops'][0]['ward_name']}")


if __name__ == "__main__":
    main()
