"""In-season engine: weekly lineup, matchup odds, predictions, power rankings, waivers.
Data: FantasyPros weekly consensus + NFL schedule via nflverse; league rosters/matchups in data/."""
import json, math, re, time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT, DATA, load_settings
from .names import norm_name, norm_team

S = load_settings()
LINEUP = [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("FLEX", 1), ("DST", 1), ("K", 1)]
FLEX_POS = {"RB", "WR", "TE"}
WEEKLY_SD = {"QB": 7.5, "RB": 7.5, "WR": 8.0, "TE": 6.0, "K": 4.0, "DST": 6.0}
ROSTERS_FILE = DATA / "league_rosters.json"
SCHED_FILE = DATA / "league_schedule.json"
_CACHE = {}


# ---------------------------------------------------------------- external data (cached ~1h)
def weekly_rankings(force=False):
    if not force and "rk" in _CACHE and time.time() - _CACHE["rk_t"] < 3600:
        return _CACHE["rk"], _CACHE["rk_date"]
    import nflreadpy as nfl
    r = nfl.load_ff_rankings("week").to_pandas()
    r = r[r["pos"].isin(["QB", "RB", "WR", "TE", "K", "DST"])].copy()
    r["key"] = r["player_name"].map(norm_name)
    r["team"] = r["team"].map(norm_team)
    r["proj"] = pd.to_numeric(r["r2p_pts"], errors="coerce")
    r["ecr"] = pd.to_numeric(r["ecr"], errors="coerce")
    r["bye"] = pd.to_numeric(r["player_bye_week"], errors="coerce")
    r = r.sort_values("ecr").drop_duplicates("key").set_index("key")
    _CACHE.update(rk=r, rk_t=time.time(), rk_date=str(r["scrape_date"].iloc[0]))
    return r, _CACHE["rk_date"]


def nfl_schedule():
    if "sch" not in _CACHE:
        import nflreadpy as nfl
        s = nfl.load_schedules([S.get("season", 2026)]).to_pandas()
        s["away_team"] = s["away_team"].map(norm_team); s["home_team"] = s["home_team"].map(norm_team)
        _CACHE["sch"] = s
    return _CACHE["sch"]


def actual_points(week):
    """Actual league-scored points by player key for a completed/in-progress week (empty before games)."""
    try:
        import nflreadpy as nfl, polars as pl
        from .scoring import weekly_points_from_history
        ck = f"act{week}"
        if ck in _CACHE and time.time() - _CACHE[ck + "_t"] < 900:
            return _CACHE[ck]
        w = nfl.load_player_stats([S.get("season", 2026)]).to_pandas()
        w = w[(w["week"] == week) & (w["season_type"] == "REG")].copy()
        if w.empty:
            out = {}
        else:
            w["pts"] = weekly_points_from_history(w, S["scoring_detail"])
            w["key"] = w["player_display_name"].map(norm_name)
            out = w.groupby("key")["pts"].sum().to_dict()
        _CACHE[ck] = out; _CACHE[ck + "_t"] = time.time()
        return out
    except Exception:
        return {}


def infer_week(sch):
    today = date.today()
    for w, g in sch.groupby("week"):
        if today <= pd.to_datetime(g["gameday"]).max().date():
            return int(w)
    return int(sch["week"].max())


def team_games(sch, week):
    out = {}
    for _, r in sch[sch["week"] == week].iterrows():
        when = f"{r['weekday'][:3]} {datetime.strptime(r['gametime'], '%H:%M').strftime('%-I:%M %p')}"
        out[r["away_team"]] = (f"@{r['home_team']}", when, f"{r['gameday']} {r['gametime']}")
        out[r["home_team"]] = (f"vs {r['away_team']}", when, f"{r['gameday']} {r['gametime']}")
    return out


# ---------------------------------------------------------------- league files
def load_rosters():
    L = json.loads(ROSTERS_FILE.read_text())
    return L


def save_rosters(L):
    ROSTERS_FILE.write_text(json.dumps(L, indent=1))


def short(team):
    return team.split(" (")[0]


def load_matchups():
    d = json.loads(SCHED_FILE.read_text()) if SCHED_FILE.exists() else {}
    return {int(k): v for k, v in d.items() if k.isdigit()}


def save_matchups(m):
    d = {"_note": "Fantasy matchups by week."}
    d.update({str(k): v for k, v in sorted(m.items())})
    SCHED_FILE.write_text(json.dumps(d, indent=1))


def parse_schedule_paste(text, team_names):
    """Parse a pasted ESPN League > Schedule page. Inside each 'NFL Week N' block, a team name is the
    line right before a record like '(0-0-0)'; consecutive team names form a matchup (away, home)."""
    lines = [l.strip() for l in text.splitlines()]
    weeks, cur = {}, None
    for i, l in enumerate(lines):
        m = re.match(r"^(?:NFL\s+)?Week\s+(\d{1,2})$", l)
        if m:
            cur = int(m.group(1)); weeks.setdefault(cur, []); continue
        if re.match(r"^Playoff", l):
            cur = None; continue
        if cur is None or not l: continue
        if i + 1 < len(lines) and re.match(r"^\(\d+-\d+-\d+\)$", lines[i + 1]):
            weeks[cur].append(l)
    out = {}
    for w, seq in weeks.items():
        pairs = [[seq[i], seq[i + 1]] for i in range(0, len(seq) - 1, 2)]
        if pairs: out[w] = pairs
    return out


def board():
    if "board" not in _CACHE:
        _CACHE["board"] = pd.read_csv(ROOT / "board.csv").set_index("key")
    return _CACHE["board"]


# ---------------------------------------------------------------- lineup math
def player_row(name, pos, rk, b, games, week, actual):
    k = norm_name(name)
    r = rk.loc[k] if k in rk.index else None
    team = r["team"] if r is not None and isinstance(r["team"], str) and r["team"] else (b.loc[k, "team"] if k in b.index else "")
    game = games.get(team)
    on_bye = (r is not None and pd.notna(r["bye"]) and int(r["bye"]) == week) or game is None
    proj = 0.0 if on_bye else (float(r["proj"]) if r is not None and pd.notna(r["proj"]) else 0.0)
    return {
        "player": name, "pos": pos, "team": team, "key": k, "proj": round(proj, 1),
        "ecr": (round(float(r["ecr"]), 1) if r is not None and pd.notna(r["ecr"]) else None),
        "grade": (r["start_sit_grade"] if r is not None and isinstance(r.get("start_sit_grade"), str) else ""),
        "opp": (game[0] if game else "BYE"), "when": (game[1] if game else ""), "lock": (game[2] if game else ""),
        "note": (r["note"] if r is not None and isinstance(r.get("note"), str) else ""),
        "tag": (r["tag"] if r is not None and isinstance(r.get("tag"), str) else ""),
        "season_proj": (round(float(b.loc[k, "proj_pts"]), 1) if k in b.index else 0.0),
        "season_vbd": (round(float(b.loc[k, "vbd"]), 1) if k in b.index else -50.0),
        "bye": bool(on_bye), "ranked": r is not None,
        "actual": (round(actual[k], 1) if k in actual else None),
    }


def best_lineup(players):
    pool = sorted(players, key=lambda p: -p["proj"])
    used, lineup = set(), []
    for slot, n in LINEUP:
        elig = [p for p in pool if p["key"] not in used and (p["pos"] in FLEX_POS if slot == "FLEX" else p["pos"] == slot)]
        for p in elig[:n]:
            lineup.append({"slot": slot, **p}); used.add(p["key"])
        for _ in range(n - len(elig[:n])):
            lineup.append({"slot": slot, "player": None})
    bench = [p for p in pool if p["key"] not in used]
    mean = sum(p["proj"] for p in lineup if p.get("player"))
    var = sum(WEEKLY_SD.get(p["pos"], 7) ** 2 for p in lineup if p.get("player") and p["proj"] > 0)
    return lineup, bench, round(mean, 1), math.sqrt(var)


def win_prob(m1, s1, m2, s2):
    return 0.5 * (1 + math.erf((m1 - m2) / math.sqrt(s1 ** 2 + s2 ** 2) / math.sqrt(2)))


# ---------------------------------------------------------------- the report
def compute(week=None, force=False):
    rk, scraped = weekly_rankings(force)
    sch = nfl_schedule()
    week = week or infer_week(sch)
    games = team_games(sch, week)
    L = load_rosters(); my_full = L["my_team"]; me_name = short(my_full)
    matchups = load_matchups()
    b = board()
    actual = actual_points(week)

    teams = {}
    for full, byp in L["rosters"].items():
        t = short(full)
        players = [player_row(p, pos, rk, b, games, week, actual) for pos, ps in byp.items() for p in ps]
        lineup, bench, mean, sd = best_lineup(players)
        def top(pos, n): return sum(sorted([p["season_proj"] for p in players if p["pos"] == pos], reverse=True)[:n])
        season = top("QB", 1) + top("RB", 2) + top("WR", 2) + top("TE", 1)
        depth = sum(sorted([p["season_vbd"] for p in players if p["pos"] in ("RB", "WR")], reverse=True)[4:9])
        act = sum(p["actual"] or 0 for p in lineup if p.get("player")) if actual else None
        teams[t] = {"name": t, "manager": full[full.find("(") + 1:-1] if "(" in full else "", "players": players, "lineup": lineup,
                    "bench": bench, "mean": mean, "sd": round(sd, 1), "season": round(season), "depth": round(depth), "actual": (round(act, 1) if act is not None else None)}

    me = teams[me_name]
    opp_name = next((bb if a == me_name else a for a, bb in matchups.get(week, []) if me_name in (a, bb)), None)
    opp = teams.get(opp_name)

    # close calls & alerts
    close = []
    for p in me["lineup"]:
        if not p.get("player") or p["slot"] in ("K", "DST", "QB"): continue
        for q in me["bench"]:
            if q["pos"] in (FLEX_POS if p["slot"] == "FLEX" else {p["slot"]}) and q["proj"] > 0 and q["proj"] >= p["proj"] - 1.5:
                close.append({"slot": p["slot"], "starter": p["player"], "starter_proj": p["proj"], "bench": q["player"], "bench_proj": q["proj"]})
    # alerts: only things that need action. Analyst start/sit opinions and notes go in their own list.
    alerts, advice = [], []
    starting = {p["key"] for p in me["lineup"] if p.get("player")}
    for p in me["players"]:
        if p["bye"]:
            alerts.append({"level": "bad" if p["key"] in starting else "warn",
                           "text": f"{p['player']} ({p['pos']}) has no game this week." + (" HE IS IN YOUR LINEUP — bench him." if p["key"] in starting else " Keep him benched.")})
        elif not p["ranked"]:
            alerts.append({"level": "bad" if p["key"] in starting else "warn",
                           "text": f"{p['player']} ({p['pos']}) is missing from this week's expert rankings, which usually means injured or benched." + (" HE IS IN YOUR LINEUP — check ESPN before kickoff." if p["key"] in starting else "")})
        tag = (p["tag"] or "").strip().lower()
        if tag in ("start", "sit") or p["note"]:
            conflict = (tag == "sit" and p["key"] in starting) or (tag == "start" and p["key"] not in starting)
            advice.append({"player": p["player"], "pos": p["pos"], "starting": p["key"] in starting, "tag": tag,
                           "conflict": bool(conflict and tag), "proj": p["proj"], "note": p["note"]})
    if not alerts:
        alerts.append({"level": "ok", "text": "No byes, nobody missing from the rankings. Check ESPN's injury tags (Q / D / O) before each game locks."})
    advice.sort(key=lambda a: (not a["conflict"], not a["starting"], -a["proj"]))

    preds = []
    for a, bb in matchups.get(week, []):
        if a not in teams or bb not in teams: continue
        A, B = teams[a], teams[bb]; wp = win_prob(A["mean"], A["sd"], B["mean"], B["sd"])
        preds.append({"a": a, "b": bb, "a_proj": A["mean"], "b_proj": B["mean"], "a_wp": round(wp, 3), "a_actual": A["actual"], "b_actual": B["actual"]})

    power = sorted(({"team": t, "manager": T["manager"], "season": T["season"], "depth": T["depth"], "week": T["mean"]} for t, T in teams.items()), key=lambda x: -x["season"])
    for i, p in enumerate(power, 1): p["rank"] = i

    rostered = {p["key"] for T in teams.values() for p in T["players"]}
    fa = b[~b.index.isin(rostered) & b["pos"].isin(["QB", "RB", "WR", "TE"])].sort_values("vbd", ascending=False)
    fa = fa[[k in rk.index and pd.notna(rk.loc[k, "proj"]) and rk.loc[k, "proj"] > 0 for k in fa.index]].head(15)
    my_worst = {pos: min([p for p in me["players"] if p["pos"] == pos], key=lambda p: p["season_vbd"], default=None) for pos in ["QB", "RB", "WR", "TE"]}
    waivers = []
    for k, r in fa.iterrows():
        w = my_worst.get(r["pos"]); gain = (r["vbd"] - w["season_vbd"]) if w else 0
        waivers.append({"player": r["player"], "pos": r["pos"], "team": r["team"], "season_vbd": round(float(r["vbd"]), 1),
                        "week_proj": round(float(rk.loc[k, "proj"]), 1), "drop": (w["player"] if w else None), "gain": round(float(gain), 1), "worth_it": bool(gain > 3)})

    first_lock = min((p["lock"] for p in me["lineup"] if p.get("player") and p["lock"]), default="")
    outlook = season_outlook(teams, matchups, week)
    return {
        "outlook": outlook,
        "week": week, "scraped": scraped, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "me": me_name, "opp": opp_name, "lineup": me["lineup"], "bench": me["bench"], "mean": me["mean"], "sd": me["sd"],
        "opp_lineup": (opp["lineup"] if opp else []), "opp_mean": (opp["mean"] if opp else None),
        "win_prob": (round(win_prob(me["mean"], me["sd"], opp["mean"], opp["sd"]), 3) if opp else None),
        "close_calls": close, "alerts": alerts, "advice": advice, "first_lock": first_lock,
        "predictions": preds, "power": power, "waivers": waivers,
        "teams": {t: {"manager": T["manager"], "players": T["players"], "mean": T["mean"], "actual": T["actual"]} for t, T in teams.items()},
        "weeks_with_matchups": sorted(matchups.keys()), "has_actuals": bool(actual),
    }


def season_outlook(teams, matchups, from_week, n=4000, seed=7):
    """Simulate the remaining regular season. Weekly score ~ N(mu, 21) where mu = season-starter strength
    per game + ~15 for K/DST. Returns per-team expected wins, P(playoffs = top 6), P(last), and
    strength of schedule (average opponent mu)."""
    names = list(teams)
    mu = {t: teams[t]["season"] / 17 + 15 for t in names}
    rng = np.random.default_rng(seed)
    wins = {t: np.zeros(n) for t in names}; pts = {t: np.zeros(n) for t in names}
    weeks = [w for w in matchups if w >= from_week and w <= 14]
    for w in weeks:
        for a, b in matchups[w]:
            if a not in teams or b not in teams: continue
            sa = rng.normal(mu[a], 21, n); sb = rng.normal(mu[b], 21, n)
            wins[a] += sa > sb; wins[b] += sb > sa; pts[a] += sa; pts[b] += sb
    W = np.column_stack([wins[t] for t in names]); Pt = np.column_stack([pts[t] for t in names])
    # rank by wins, tiebreak points
    order = np.lexsort((-Pt, -W), axis=1) if False else None
    score = W * 1e6 + Pt
    ranks = (-score).argsort(axis=1).argsort(axis=1) + 1
    out = []
    for i, t in enumerate(names):
        opp_mu = [mu[b if a == t else a] for w in weeks for a, b in matchups[w] if t in (a, b)]
        out.append({"team": t, "exp_wins": round(float(W[:, i].mean()), 1), "p_playoffs": round(float((ranks[:, i] <= 6).mean()), 3),
                    "p_last": round(float((ranks[:, i] == 12).mean()), 3), "p_top": round(float((ranks[:, i] == 1).mean()), 3),
                    "sos": round(float(np.mean(opp_mu)) if opp_mu else 0, 1), "mu": round(mu[t], 1)})
    return sorted(out, key=lambda x: -x["exp_wins"])


def render_markdown(d):
    o = []; P = o.append
    P(f"# Week {d['week']} report — {S.get('league_name', '')}\n\nExpert rankings scraped {d['scraped']}.\n")
    P("## Lineup\n\n| Slot | Start | Opp | Game | Proj | Grade |\n|---|---|---|---|---|---|")
    for p in d["lineup"]:
        P(f"| {p['slot']} | {p['player'] or 'EMPTY'} | {p.get('opp','')} | {p.get('when','')} | {p.get('proj','')} | {p.get('grade','')} |")
    P(f"\nProjected {d['mean']}. Earliest lock {d['first_lock']}.\n\n## Alerts\n")
    for a in d["alerts"]: P(f"- {a['text']}")
    if d["opp"]: P(f"\n## Matchup\n\nvs {d['opp']}: {d['mean']} to {d['opp_mean']}, win probability {d['win_prob']:.0%}.\n")
    P("## Predictions\n\n| Matchup | Proj | Proj | Favorite | Win % |\n|---|---|---|---|---|")
    for p in d["predictions"]:
        fav = p["a"] if p["a_wp"] >= 0.5 else p["b"]; P(f"| {p['a']} vs {p['b']} | {p['a_proj']} | {p['b_proj']} | {fav} | {max(p['a_wp'], 1-p['a_wp']):.0%} |")
    P("\n## Power rankings\n\n| # | Team | Season | Depth | This week |\n|---|---|---|---|---|")
    for p in d["power"]: P(f"| {p['rank']} | {p['team']} | {p['season']} | {p['depth']} | {p['week']} |")
    P("\n## Waiver targets\n\n| Player | Pos | Season value | This week | Drop | Gain |\n|---|---|---|---|---|---|")
    for w in d["waivers"]: P(f"| {w['player']} | {w['pos']} | {w['season_vbd']} | {w['week_proj']} | {w['drop']} | {w['gain']} |")
    return "\n".join(o)
