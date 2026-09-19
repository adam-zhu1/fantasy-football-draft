"""Live in-game scoring from ESPN's public scoreboard and box scores.

Why this exists: nflverse publishes finals hours late. On Sun Sep 13 2026 at 4:15pm ET it
still listed BAL@IND as unplayed when it had finished 41-23, so the weekly report showed the
opponent on 35.3 when he was really on 104.26 and put the matchup at 75% the wrong way.

ESPN's site API updates within about a minute and needs no key. Scoring its box score with
this league's own rules from settings.json reproduces ESPN's fantasy points exactly
(checked Week 1 2026: Henry 35.3, Flowers 26.0, Loop 14.0, Allen 9.2).

Public entry point is `live_week`. Everything degrades to empty on a network failure so the
caller can fall back to nflverse.
"""
import re
import time

import requests

from .config import load_settings
from .names import norm_name, norm_team

S = load_settings()
API = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
TIMEOUT = 12
_CACHE = {}
_TTL = 45          # seconds; ESPN updates roughly every minute during a game

# A regulation game is 60 minutes of clock. Used to judge how much of a player's
# projection is still ahead of him while his game is in progress.
GAME_MINUTES = 60.0

_FG = re.compile(r"^(.*?)\s+(\d+)\s+Yd\s+Field\s+Goal", re.I)
_SAFETY = re.compile(r"\bsafety\b", re.I)
_TWO_PT = re.compile(r"\(([^)]*?)two-point conversion[^)]*\)", re.I)
_NAME_IN_2PT = re.compile(r"([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+)+)")


def _get(url, ttl=None):
    """GET and parse JSON, cached briefly. Returns None on any failure."""
    hit = _CACHE.get(url)
    if hit and time.time() - hit[0] < (_TTL if ttl is None else ttl):
        return hit[1]
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()
    except Exception:
        return hit[1] if hit else None
    _CACHE[url] = (time.time(), data)
    return data


# ------------------------------------------------------------------ game state
def _fraction_left(status):
    """How much of the game is still to be played, 0.0 (final) to 1.0 (not started).

    Overtime counts as nearly over: the projection has already been spent, so only a
    sliver of scoring is still plausible.
    """
    t = (status or {}).get("type", {})
    state = t.get("state")
    if state == "post" or t.get("completed"):
        return 0.0
    if state == "pre":
        return 1.0
    period = status.get("period") or 1
    if period > 4:
        return 0.05
    clock = status.get("displayClock") or "15:00"
    try:
        mm, ss = clock.split(":")
        remaining_in_period = int(mm) + int(ss) / 60.0
    except Exception:
        remaining_in_period = 15.0
    left = (4 - period) * 15.0 + remaining_in_period
    return max(0.0, min(1.0, left / GAME_MINUTES))


def scoreboard(week, season=None):
    """Week's games from ESPN: event id, both teams, state and fraction of game left."""
    season = season or S.get("season", 2026)
    d = _get(f"{API}/scoreboard?week={week}&seasontype=2&dates={season}")
    if not d:
        return []
    out = []
    for e in d.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        status = comp.get("status", {})
        teams = [norm_team(c.get("team", {}).get("abbreviation")) for c in comp.get("competitors", [])]
        scores = {}
        for c in comp.get("competitors", []):
            try:
                scores[norm_team(c.get("team", {}).get("abbreviation"))] = int(c.get("score") or 0)
            except (TypeError, ValueError):
                pass
        out.append({"id": e.get("id"), "teams": [t for t in teams if t], "scores": scores,
                    "state": status.get("type", {}).get("state"),
                    "detail": status.get("type", {}).get("detail", ""),
                    "left": _fraction_left(status)})
    return out


# ------------------------------------------------------------------ box scores
def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


def _pair(v):
    """'2/3' -> (2.0, 3.0)."""
    try:
        made, att = str(v).split("/")
        return _num(made), _num(att)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _sections(team_block):
    """{'rushing': (labels, [(athlete_name, stats)]), ...} for one team's player box score."""
    out = {}
    for st in team_block.get("statistics", []):
        rows = []
        for a in st.get("athletes", []):
            name = (a.get("athlete") or {}).get("displayName")
            if name:
                rows.append((name, dict(zip(st.get("labels", []), a.get("stats", [])))))
        out[st.get("name")] = {"labels": st.get("labels", []), "rows": rows,
                               "totals": dict(zip(st.get("labels", []), st.get("totals", [])))}
    return out


def _offense_points(sections, sc):
    """League-scored offensive points per player key from one team's box score."""
    pts = {}

    def add(name, n):
        # Record every athlete who appears, including a genuine zero. Skipping zeros would
        # make a player who dressed and did nothing look like a missing box score, and the
        # caller would fall back to his projection.
        k = norm_name(name)
        pts[k] = pts.get(k, 0.0) + n

    for name, s in sections.get("passing", {}).get("rows", []):
        add(name, _num(s.get("YDS")) * sc["pass_yd"] + _num(s.get("TD")) * sc["pass_td"]
            + _num(s.get("INT")) * sc["int"])
    for name, s in sections.get("rushing", {}).get("rows", []):
        add(name, _num(s.get("YDS")) * sc["rush_yd"] + _num(s.get("TD")) * sc["rush_td"])
    for name, s in sections.get("receiving", {}).get("rows", []):
        add(name, _num(s.get("REC")) * sc["rec"] + _num(s.get("YDS")) * sc["rec_yd"]
            + _num(s.get("TD")) * sc["rec_td"])
    for name, s in sections.get("fumbles", {}).get("rows", []):
        add(name, _num(s.get("LOST")) * sc["fumble_lost"])
    for sec in ("kickReturns", "puntReturns"):
        for name, s in sections.get(sec, {}).get("rows", []):
            add(name, _num(s.get("TD")) * sc.get("special_teams_td", 6))
    return pts


def _kicker_points(sections, scoring_plays, team_abbr, kd):
    """Kickers by made-field-goal distance, extra points, and missed field goals.

    Distances only exist in the scoring plays, so made FGs are matched by kicker name and
    anything unmatched falls back to the shortest bucket.
    """
    pts = {}
    made_by_kicker = {}
    for text in scoring_plays:
        m = _FG.match(text.strip())
        if not m:
            continue
        made_by_kicker.setdefault(norm_name(m.group(1)), []).append(_num(m.group(2)))
    for name, s in sections.get("kicking", {}).get("rows", []):
        key = norm_name(name)
        made, att = _pair(s.get("FG"))
        xp_made, _ = _pair(s.get("XP"))
        total = xp_made * kd.get("pat", 1) + max(0.0, att - made) * kd.get("fg_missed", -1)
        dists = made_by_kicker.get(key, [])
        for d in dists[:int(made)]:
            if d >= 60:
                total += kd.get("fg_60_plus", 6)
            elif d >= 50:
                total += kd.get("fg_50_59", 5)
            elif d >= 40:
                total += kd.get("fg_40_49", 4)
            else:
                total += kd.get("fg_0_39", 3)
        for _ in range(int(made) - len(dists[:int(made)])):
            total += kd.get("fg_0_39", 3)
        pts[key] = pts.get(key, 0.0) + total
    return pts


def _bracket(table, value):
    for lo, hi, points in table or []:
        if lo <= value <= hi:
            return points
    return 0


def _dst_points(sections, opp_sections, opp_team_stats, opp_score, safeties, dd):
    """One team defense's points: its own splash plays, the opponent's giveaways,
    and the points and yards the opponent put up."""
    d = sections.get("defensive", {}).get("totals", {})
    ints = sections.get("interceptions", {}).get("totals", {})
    total = 0.0
    total += _num(d.get("SACKS")) * dd.get("sack", 1)
    total += _num(ints.get("INT")) * dd.get("interception", 2)
    total += _num(opp_team_stats.get("fumblesLost")) * dd.get("fumble_recovery", 2)
    total += _num(opp_team_stats.get("defensiveTouchdowns_self")) * dd.get("return_td", 6)
    for sec in ("kickReturns", "puntReturns"):
        total += _num(sections.get(sec, {}).get("totals", {}).get("TD")) * dd.get("return_td", 6)
    total += safeties * dd.get("safety", 2)
    total += _bracket(dd.get("points_allowed"), opp_score)
    total += _bracket(dd.get("yards_allowed"), _num(opp_team_stats.get("totalYards")))
    return total


def game_points(event_id, team_full_names):
    """League-scored points for every player and team defense in one game.

    Returns {player_key_or_dst_name: points}. Empty if ESPN is unreachable.
    """
    d = _get(f"{API}/summary?event={event_id}")
    if not d:
        return {}
    sc = S["scoring_detail"]
    kd = S.get("kicking_detail", {})
    dd = S.get("dst_detail", {})
    plays = [p.get("text", "") for p in d.get("scoringPlays", [])]

    blocks = {}
    for tb in d.get("boxscore", {}).get("players", []):
        abbr = norm_team((tb.get("team") or {}).get("abbreviation"))
        blocks[abbr] = _sections(tb)
    team_stats, scores = {}, {}
    for tb in d.get("boxscore", {}).get("teams", []):
        abbr = norm_team((tb.get("team") or {}).get("abbreviation"))
        stats = {}
        for s in tb.get("statistics", []):
            stats.setdefault(s.get("name"), s.get("displayValue"))
        team_stats[abbr] = stats
    for c in (d.get("header", {}).get("competitions") or [{}])[0].get("competitors", []):
        abbr = norm_team((c.get("team") or {}).get("abbreviation"))
        try:
            scores[abbr] = int(c.get("score") or 0)
        except (TypeError, ValueError):
            scores[abbr] = 0

    out = {}
    for abbr, sections in blocks.items():
        out.update(_offense_points(sections, sc))
        out.update(_kicker_points(sections, plays, abbr, kd))
    # two-point conversions: the box score has no column for them, so read the play text
    for text in plays:
        m = _TWO_PT.search(text)
        if not m:
            continue
        for who in _NAME_IN_2PT.findall(m.group(1)):
            k = norm_name(who)
            if k in out:
                out[k] = out[k] + sc.get("two_pt", 2)

    # team defenses
    opponents = list(blocks)
    for abbr, sections in blocks.items():
        opp = next((o for o in opponents if o != abbr), None)
        if opp is None:
            continue
        opp_stats = dict(team_stats.get(opp, {}))
        opp_stats["defensiveTouchdowns_self"] = team_stats.get(abbr, {}).get("defensiveTouchdowns", 0)
        safeties = sum(1 for t in plays if _SAFETY.search(t) and abbr in t.upper())
        full = team_full_names.get(abbr)
        if full:
            out[norm_name(full)] = _dst_points(sections, blocks.get(opp, {}), opp_stats,
                                               scores.get(opp, 0), safeties, dd)
    return out


# ------------------------------------------------------------------ public API
def live_week(week, team_full_names, season=None):
    """Everything the weekly report needs about games in flight.

    Returns {"points":  {key: league points so far},
             "left":    {team abbr: fraction of that team's game still to play},
             "final":   {team abbrs whose game is over},
             "covered": {team abbrs whose box score we actually read, so a rostered player
                         missing from it was inactive and scored nothing},
             "ok":      whether ESPN answered}.
    """
    games = scoreboard(week, season)
    if not games:
        return {"points": {}, "left": {}, "final": set(), "covered": set(), "ok": False}
    points, left, final, covered = {}, {}, set(), set()
    for g in games:
        for t in g["teams"]:
            left[t] = g["left"]
            if g["state"] == "post":
                final.add(t)
        if g["state"] in ("in", "post"):
            got = game_points(g["id"], team_full_names)
            if got:
                points.update(got)
                covered.update(g["teams"])
    return {"points": points, "left": left, "final": final, "covered": covered, "ok": True}


def team_full_names():
    """{'SEA': 'Seattle Seahawks', ...} so team defenses can be matched to roster entries."""
    d = _get(f"{API}/teams")
    out = {}
    if not d:
        return out
    for grp in d.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        t = grp.get("team", {})
        if t.get("abbreviation") and t.get("displayName"):
            out[norm_team(t["abbreviation"])] = t["displayName"]
    return out


# ------------------------------------------------------------------ injuries
# Statuses that mean the player will not take the field. "Doubtful" is left out
# deliberately: it is a strong warning, not a certainty, so it is flagged loudly but the
# projection is left alone rather than zeroed.
OUT_STATUSES = {"Out", "Injured Reserve", "Suspension", "Physically Unable to Perform",
                "Non Football Injury", "Practice Squad"}
_INJ_TTL = 1800     # the payload is several megabytes and designations move slowly


def injuries():
    """{player key: (status, body part)} for everyone ESPN lists with a designation.

    Closes the gap where the report told you to go and check ESPN's Q / D / O tags yourself.
    """
    d = _get(f"{API}/injuries", ttl=_INJ_TTL)
    out = {}
    if not d:
        return out
    for team in d.get("injuries", []):
        for e in team.get("injuries", []):
            a = e.get("athlete") or {}
            key = norm_name(a.get("displayName"))
            status = e.get("status")
            if key and status and status != "Active":
                out[key] = (status, ((e.get("details") or {}) or {}).get("type") or "")
    return out
