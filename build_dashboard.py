#!/usr/bin/env python
"""Write dashboard.html: the season dashboard with its data baked in, no server needed.

The dashboard used to require `season_server.py` to be running. That is a process, and a
process can die or go stale without saying so: one started on Sep 9 2026 was still serving
two-week-old code on Sep 22, and nothing on the page gave it away. A built file cannot rot
that way. Every build runs the current code from scratch, and the file states when it was
made, so a stale one is visible rather than silent.

Run it from a LaunchAgent and open the file. The server is still there for when you want a
recompute on demand.

    python build_dashboard.py            # build if due (see below)
    python build_dashboard.py --force    # build now
    python build_dashboard.py --weeks 3  # only the weeks listed, instead of 1..current
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from ffdraft.config import ROOT
from ffdraft.season import compute, infer_week, nfl_schedule

OUT = ROOT / "dashboard.html"
TEMPLATE = ROOT / "templates" / "season.html"

# The agent fires every 10 minutes; this decides whether there is any point. Games move the
# numbers, and nothing else does, so rebuild fast while they are on and hourly otherwise.
# Weekday from Python's Monday=0: Thursday, Sunday, Monday.
GAME_WINDOWS = {3: (17, 24), 6: (9, 24), 0: (17, 24)}
FAST_MINUTES = 10
SLOW_MINUTES = 60


def due(now=None):
    """(should_build, why)."""
    now = now or datetime.now()
    if not OUT.exists():
        return True, "no dashboard.html yet"
    age = (time.time() - OUT.stat().st_mtime) / 60
    lo, hi = GAME_WINDOWS.get(now.weekday(), (99, 99))
    live = lo <= now.hour < hi
    need = FAST_MINUTES if live else SLOW_MINUTES
    if age >= need:
        return True, f"{age:.0f} min old, rebuilding every {need} min {'during games' if live else 'off game days'}"
    return False, f"{age:.0f} min old, next build at {need} min"


def build(weeks=None):
    sch = nfl_schedule()
    current = infer_week(sch)
    weeks = weeks or list(range(1, current + 1))
    data = {}
    for w in weeks:
        try:
            data[str(w)] = compute(w)
        except Exception as e:                      # one bad week should not cost the rest
            print(f"  week {w}: skipped ({type(e).__name__}: {e})")
    if not data:
        raise SystemExit("nothing built")
    payload = {"week": current if str(current) in data else max(int(k) for k in data),
               "built": datetime.now().strftime("%a %b %-d, %-I:%M %p"), "weeks": data}
    html = TEMPLATE.read_text()
    inline = ("<script>window.INLINE = "
              + json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
              + ";</script>\n<script>")
    # Plain replace, not re.sub: the payload is full of backslash escapes that a regex
    # replacement string would try to interpret.
    html = html.replace("<script>", inline, 1)
    OUT.write_text(html)
    return current, sorted(data, key=int), OUT.stat().st_size


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--weeks", type=int, nargs="+")
    a = ap.parse_args()
    go, why = (True, "forced") if a.force else due()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not go:
        print(f"[{stamp}] skip — {why}")
        raise SystemExit(0)
    print(f"[{stamp}] build — {why}")
    week, built, size = build(a.weeks)
    print(f"[{stamp}] wrote {OUT} ({size/1024:.0f} KB), weeks {', '.join(built)}, current {week}")
