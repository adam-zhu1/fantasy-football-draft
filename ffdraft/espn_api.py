"""Read the league from ESPN's fantasy API using the manager's own session cookies.

Everything else in this tool sees only public data. A box score says what a player scored;
it does not say which players a manager chose to start. That gap was the single largest
error in the model: opponents' weekly totals ran about 10 points off, because a lineup we
cannot see was assumed to be the best one that roster allowed. Real managers neither find
that lineup nor miss it consistently, so the error ran both ways and did not average out.
Those totals feed team ratings, which feed the playoff number the dashboard steers by.

The league is private ("Make League Viewable to Public: No"), so the feed needs
`data/espn_auth.json` holding the `espn_s2` and `SWID` cookies from a logged-in browser:

    {"league_id": <your league id>, "season": 2026, "swid": "{...}", "espn_s2": "..."}

That file is gitignored and chmod 600; it is a login credential, not a config value. When it
is absent or the cookies have expired, every function here returns None and callers fall
back to the old projection-based guess, so the tool still runs for anyone without it.
"""
import json
import time

import requests

from .config import DATA
from .names import norm_name

AUTH_FILE = DATA / "espn_auth.json"
DISK_DIR = DATA / "cache" / "espn"
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
TIMEOUT = 20
TTL = 300

# ESPN's lineupSlotId. Anything not named here (20 bench, 21 IR, and the slots this league
# does not use) is not a starter.
SLOTS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "DST", 17: "K", 23: "FLEX"}
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}

_CACHE = {}


def auth():
    """Credentials, or None if they were never set up."""
    if "auth" not in _CACHE:
        try:
            _CACHE["auth"] = json.loads(AUTH_FILE.read_text())
        except (OSError, ValueError):
            _CACHE["auth"] = None
    return _CACHE["auth"]


def available():
    return auth() is not None


def _get(views, week=None, season_level=False, disk_key=None):
    """One cached GET. Returns None rather than raising: a dead feed should degrade the
    report, not kill it mid-run on a Sunday morning.

    `disk_key` keeps the response between runs, for a week that is over and whose lineups
    can no longer change. Each of these is about 2.3 MB, so a scheduled build refetching
    every played week adds up fast.
    """
    a = auth()
    if a is None:
        return None
    key = (tuple(sorted(views)), week, season_level)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[1] < TTL:
        return hit[0]
    if disk_key:
        f = DISK_DIR / f"{disk_key}.json"
        try:
            d = json.loads(f.read_text())
            _CACHE[key] = (d, time.time())
            return d
        except (OSError, ValueError):
            pass
    url = f"{BASE}/{a['season']}" if season_level else \
          f"{BASE}/{a['season']}/segments/0/leagues/{a['league_id']}"
    params = {"view": list(views)}
    if week is not None:
        params["scoringPeriodId"] = week
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT,
                         cookies={"espn_s2": a["espn_s2"], "SWID": a["swid"]},
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        d = r.json()
    except (requests.RequestException, ValueError):
        return None
    _CACHE[key] = (d, time.time())
    if disk_key:
        try:
            DISK_DIR.mkdir(parents=True, exist_ok=True)
            (DISK_DIR / f"{disk_key}.json").write_text(json.dumps(d))
        except OSError:
            pass
    return d


def pro_teams():
    """{proTeamId: 'Kansas City Chiefs'} so a D/ST matches its roster spelling."""
    if "pro" not in _CACHE:
        d = _get(["proTeamSchedules_wl"], season_level=True)
        if d is None:
            return {}
        _CACHE["pro"] = {t["id"]: f"{t.get('location', '')} {t.get('name', '')}".strip()
                         for t in d["settings"]["proTeams"] if t.get("name")}
    return _CACHE["pro"]


def _player_name(p):
    """Roster spelling for a player or a defense."""
    if p.get("defaultPositionId") == 16:
        return pro_teams().get(p.get("proTeamId")) or p.get("fullName", "")
    return p.get("fullName", "")


def team_labels():
    """{espn team id: ('green fn', 'green fn (Adam Zhu)')}, matching league_rosters.json keys."""
    d = _get(["mTeam"])
    if d is None:
        return {}
    who = {m["id"]: f"{m.get('firstName', '').strip()} {m.get('lastName', '').strip()}".strip()
           for m in d.get("members", [])}
    out = {}
    for t in d["teams"]:
        name = (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip()
        owner = next((who.get(o) for o in t.get("owners") or [] if who.get(o)), "")
        out[t["id"]] = (name, f"{name} ({owner})" if owner else name)
    return out


def current_week():
    d = _get(["mTeam"])
    return d.get("scoringPeriodId") if d else None


def records():
    """{'green fn (Adam Zhu)': '2-0-0'}."""
    d = _get(["mTeam"])
    if d is None:
        return {}
    lab = team_labels()
    out = {}
    for t in d["teams"]:
        r = t["record"]["overall"]
        out[lab[t["id"]][1]] = f"{r['wins']}-{r['losses']}-{r['ties']}"
    return out


def rosters():
    """Full rosters in league_rosters.json shape, or None if the feed is unreachable.

    This replaces pasting the League > Rosters page by hand, which went stale the moment
    anyone touched the waiver wire.
    """
    # Deliberately the same request lineups() makes for the current week, so the two share
    # one cache entry instead of pulling the same 2.3 MB twice per build. The per-week roster
    # carries every entry, bench and IR included, which is all this needs.
    d = _get(["mRoster"], week=current_week())
    if d is None:
        return None
    lab = team_labels()
    out = {}
    for t in d["teams"]:
        byp = {}
        for e in t["roster"]["entries"]:
            p = e["playerPoolEntry"]["player"]
            pos = POSITIONS.get(p.get("defaultPositionId"))
            if pos:
                byp.setdefault(pos, []).append(_player_name(p))
        out[lab[t["id"]][1]] = {k: byp[k] for k in ("QB", "RB", "WR", "TE", "DST", "K") if k in byp}
    return out


def lineups(week):
    """{'green fn': [['QB', 'patrickmahomes'], ...]} — who each manager ACTUALLY started.

    Same shape as the frozen snapshots in week_lineups.json, so it drops straight in.
    """
    cur = current_week()
    over = cur is not None and week < cur
    d = _get(["mRoster"], week=week, disk_key=f"lineups_w{week}" if over else None)
    if d is None:
        return None
    lab = team_labels()
    if not lab:
        return None
    out = {}
    for t in d["teams"]:
        starters = []
        for e in t["roster"]["entries"]:
            slot = SLOTS.get(e.get("lineupSlotId"))
            if slot:
                starters.append([slot, norm_name(_player_name(e["playerPoolEntry"]["player"]))])
        if starters:
            out[lab[t["id"]][0]] = starters
    return out or None


def team_totals(week):
    """{'green fn': 162.08} — ESPN's own final for the week. Authoritative: it already
    accounts for stat corrections, and for players since dropped whom our roster no
    longer lists."""
    d = _get(["mMatchupScore", "mTeam"], week=week)
    if d is None:
        return None
    lab = team_labels()
    out = {}
    for m in d.get("schedule", []):
        if m.get("matchupPeriodId") != week:
            continue
        for side in ("home", "away"):
            t = m.get(side)
            if t and t.get("teamId") in lab:
                out[lab[t["teamId"]][0]] = round(float(t.get("totalPoints") or 0.0), 2)
    return out or None


def sync_lineups(snaps, weeks):
    """Overwrite frozen lineup snapshots with the real ones.

    Returns (changed, real_weeks): whether anything moved, and which weeks the feed could
    actually speak for, so the report can say which numbers rest on real lineups and which
    are still the old guess.

    Real lineups win over ours unconditionally, including for weeks already recorded: a
    snapshot we guessed is not evidence, it is the error we are here to remove.
    """
    changed, real = False, set()
    for w in weeks:
        got = lineups(w)
        if not got:
            continue
        real.add(int(w))
        if snaps.get(str(w)) != got:
            snaps[str(w)] = got
            changed = True
    return changed, real
