"""
Assign each hotspot its real BBMP ward (point-in-polygon).
No Mappls calls, no credit, no crash risk. Run once.

Needs in the same folder:
  - hotspots_full.json   (from build_data.py)
  - BBMP.geojson         (the ward file you downloaded)

Adds 'ward_no' and 'ward_name' to each hotspot and rewrites hotspots_full.json.
"""

import json
from shapely.geometry import shape, Point

with open("BBMP.geojson") as f:
    wards = json.load(f)["features"]
ward_shapes = [(shape(w["geometry"]),
                w["properties"].get("KGISWardName", ""),
                str(w["properties"].get("KGISWardNo", ""))) for w in wards]

with open("hotspots_full.json") as f:
    hotspots = json.load(f)

def find_ward(lat, lng):
    p = Point(lng, lat)  # GeoJSON order is lng,lat
    for geom, name, no in ward_shapes:
        if geom.contains(p):
            return no, name
    return "", "Outside BBMP"

for h in hotspots:
    no, name = find_ward(h["lat"], h["lng"])
    h["ward_no"] = no
    h["ward_name"] = name

with open("hotspots_full.json", "w") as f:
    json.dump(hotspots, f, indent=2)

# summary
from collections import Counter
by_ward = Counter(h["ward_name"] for h in hotspots)
print(f"Assigned wards to {len(hotspots)} hotspots.")
print("\nHotspots per ward:")
for ward, n in by_ward.most_common():
    print(f"  {n:2d}  {ward}")
print("\nDone — hotspots_full.json now has ward_no + ward_name.")
print("Re-copy it into your React public/ folder.")