"""
Recompute scheduler — the "recomputed every 5–15 min" loop from the CIS spec.

Dependency-light (no APScheduler): on each tick it ingests the latest
cis_hotspots.json into the DB as a new timestamped snapshot. Optionally it first
re-runs the scoring pipeline (--rescore) — heavy, so the default is to just
ingest whatever the pipeline most recently produced.

Run:
  python scheduler.py --interval 600                 # ingest every 10 min
  python scheduler.py --interval 900 --rescore       # also re-run the pipeline
  python scheduler.py --once                          # single tick (for cron/Task Scheduler)
"""
import argparse, os, subprocess, sys, time

import db


def tick(rescore=False, json_path=None):
    if rescore:
        print("[scheduler] re-running scoring pipeline (heavy)...")
        env = dict(os.environ, GRIDLOCK_OUT="outputs")
        subprocess.run([sys.executable, "gridlock_pipeline.py"], env=env, check=False)
        # refresh derived artefacts
        subprocess.run([sys.executable, "patrol_routing.py"], check=False)
    src = json_path
    if not src:
        for c in ["../frontend/public/cis_hotspots.json", "outputs/cis_hotspots.json"]:
            if os.path.exists(c):
                src = c; break
    if not src:
        print("[scheduler] no cis_hotspots.json found; skipping tick")
        return
    n = db.ingest(src)
    print(f"[scheduler] ingested {n} records from {src} at {time.strftime('%H:%M:%S')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=600, help="seconds between ticks")
    ap.add_argument("--rescore", action="store_true", help="re-run the pipeline each tick")
    ap.add_argument("--once", action="store_true", help="run a single tick and exit")
    args = ap.parse_args()

    if args.once:
        tick(args.rescore)
        return
    print(f"[scheduler] every {args.interval}s (rescore={args.rescore}). Ctrl+C to stop.")
    while True:
        try:
            tick(args.rescore)
        except Exception as e:
            print(f"[scheduler] tick error (continuing): {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
