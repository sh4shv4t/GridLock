"""
SQLite persistence for CIS records — the storage layer for a live service.

Today the pipeline emits cis_hotspots.json (batch). This ingests that into a
queryable DB so the API can serve from storage and the scheduler can recompute
on an interval without re-reading files. Keeps a `computed_at` timestamp per
ingest so you get a history of score snapshots, not just the latest.

CLI:
  python db.py ingest [path]     # load cis_hotspots.json into gridlock.db
  python db.py top --limit 10    # show top hotspots by CIS
"""
import argparse, json, os, sqlite3, time

DB_PATH = os.environ.get("GRIDLOCK_DB", "gridlock.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cis_scores (
  hotspot_id TEXT, window_start TEXT, computed_at REAL,
  cis REAL, class TEXT, confidence REAL, low_confidence INTEGER,
  violation_load REAL, carriageway_obstruction REAL, excess_congestion REAL, recurrence REAL,
  explanation TEXT, lat REAL, lng REAL, ward_name TEXT,
  PRIMARY KEY (hotspot_id, computed_at)
);
CREATE INDEX IF NOT EXISTS idx_cis ON cis_scores (cis DESC);
CREATE INDEX IF NOT EXISTS idx_latest ON cis_scores (computed_at DESC);
"""


def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def ingest(path_json, db_path=DB_PATH, computed_at=None):
    computed_at = computed_at or time.time()
    records = json.load(open(path_json, encoding="utf-8"))
    con = connect(db_path)
    rows = []
    for r in records:
        comp = r.get("components", {})
        rows.append((
            r["hotspot_id"], r.get("window_start"), computed_at,
            r.get("cis"), r.get("class"), r.get("confidence"), int(bool(r.get("low_confidence"))),
            comp.get("violation_load"), comp.get("carriageway_obstruction"),
            comp.get("excess_congestion"), comp.get("recurrence"),
            json.dumps(r.get("explanation", [])), r.get("lat"), r.get("lng"), r.get("ward_name", ""),
        ))
    con.executemany("INSERT OR REPLACE INTO cis_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM cis_scores WHERE computed_at=?", (computed_at,)).fetchone()[0]
    con.close()
    return n


def _row_to_contract(row):
    return {
        "hotspot_id": row["hotspot_id"], "window_start": row["window_start"],
        "cis": row["cis"], "class": row["class"], "confidence": row["confidence"],
        "components": {
            "violation_load": row["violation_load"],
            "carriageway_obstruction": row["carriageway_obstruction"],
            "excess_congestion": row["excess_congestion"], "recurrence": row["recurrence"],
        },
        "explanation": json.loads(row["explanation"] or "[]"),
        "low_confidence": bool(row["low_confidence"]),
        "lat": row["lat"], "lng": row["lng"], "ward_name": row["ward_name"],
        "computed_at": row["computed_at"],
    }


def latest_snapshot(con):
    row = con.execute("SELECT MAX(computed_at) AS t FROM cis_scores").fetchone()
    return row["t"]


def query_top(db_path=DB_PATH, limit=100, cls=None, min_cis=0.0):
    con = connect(db_path)
    t = latest_snapshot(con)
    q = "SELECT * FROM cis_scores WHERE computed_at=? AND cis>=?"
    args = [t, min_cis]
    if cls:
        q += " AND class=?"; args.append(cls)
    q += " ORDER BY cis DESC LIMIT ?"; args.append(limit)
    out = [_row_to_contract(r) for r in con.execute(q, args).fetchall()]
    con.close()
    return out


def get_by_id(hotspot_id, db_path=DB_PATH):
    con = connect(db_path)
    t = latest_snapshot(con)
    r = con.execute("SELECT * FROM cis_scores WHERE hotspot_id=? AND computed_at=?",
                    (hotspot_id, t)).fetchone()
    con.close()
    return _row_to_contract(r) if r else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    ig = sub.add_parser("ingest"); ig.add_argument("path", nargs="?", default=None)
    tp = sub.add_parser("top"); tp.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    if args.cmd == "ingest":
        src = args.path
        if not src:
            for c in ["../frontend/public/cis_hotspots.json", "outputs/cis_hotspots.json", "../model/outputs/cis_hotspots.json"]:
                if os.path.exists(c): src = c; break
        n = ingest(src)
        print(f"Ingested {n} records from {src} into {DB_PATH}")
    elif args.cmd == "top":
        for r in query_top(limit=args.limit):
            print(f"  {r['cis']:5.1f} {r['class']:8s} {r['ward_name'][:28]:28s} {r['hotspot_id']}")
    else:
        ap.print_help()
