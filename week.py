#!/usr/bin/env python
"""Part 5: weekly report. Lineup to enter in ESPN, matchup predictions, power rankings, alerts, waiver targets.

    python week.py            # current week (inferred from the NFL schedule)
    python week.py --week 3

Data: FantasyPros weekly expert consensus (projected points, start/sit grades) and the NFL schedule via
nflverse; league rosters from data/league_rosters.json; fantasy matchups from data/league_schedule.json;
season values from board.csv.
"""
import argparse, json, math, sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ffdraft.config import ROOT, DATA, load_settings
from ffdraft.names import norm_name, norm_team

S = load_settings()
LINEUP = [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("FLEX", 1), ("DST", 1), ("K", 1)]
FLEX_POS = {"RB", "WR", "TE"}
WEEKLY_SD = {"QB": 7.5, "RB": 7.5, "WR": 8.0, "TE": 6.0, "K": 4.0, "DST": 6.0}   # from 2023-25 history (tiers 1-3)
MY_TEAM_SHORT = "green fn"


# ---------------------------------------------------------------- data
def load_weekly_rankings():
    import nflreadpy as nfl
    r = nfl.load_ff_rankings("week").to_pandas()
    r = r[r["pos"].isin(["QB", "RB", "WR", "TE", "K", "DST"])].copy()
    r["key"] = r["player_name"].map(norm_name)
    r["team"] = r["team"].map(norm_team)
    # DST rows are team names like "Seattle Seahawks"; make their key match rosters
    r["proj"] = pd.to_numeric(r["r2p_pts"], errors="coerce")
    r["ecr"] = pd.to_numeric(r["ecr"], errors="coerce")
    r["bye"] = pd.to_numeric(r["player_bye_week"], errors="coerce")
    r = r.sort_values("ecr").drop_duplicates("key")
    return r.set_index("key"), str(r["scrape_date"].iloc[0])


def load_schedule():
    import nflreadpy as nfl
    s = nfl.load_schedules([S.get("season", 2026)]).to_pandas()
    s["away_team"] = s["away_team"].map(norm_team); s["home_team"] = s["home_team"].map(norm_team)
    return s


def infer_week(sch):
    today = date.today()
    for w, g in sch.groupby("week"):
        last = pd.to_datetime(g["gameday"]).max().date()
        if today <= last:
            return int(w)
    return int(sch["week"].max())


def team_games(sch, week):
    g = sch[sch["week"] == week]
    out = {}
    for _, r in g.iterrows():
        when = f"{r['weekday'][:3]} {datetime.strptime(r['gametime'], '%H:%M').strftime('%-I:%M %p')}"
        out[r["away_team"]] = (f"@{r['home_team']}", when, r["gameday"], r["gametime"])
        out[r["home_team"]] = (f"vs {r['away_team']}", when, r["gameday"], r["gametime"])
    return out


def load_league():
    L = json.loads((DATA / "league_rosters.json").read_text())
    rosters = {}
    for team, byp in L["rosters"].items():
        short = team.split(" (")[0]
        rosters[short] = [(p, pos) for pos, ps in byp.items() for p in ps]
    sched = json.loads((DATA / "league_schedule.json").read_text())
    return rosters, {int(k): v for k, v in sched.items() if k.isdigit()}


def load_board():
    b = pd.read_csv(ROOT / "board.csv")
    return b.set_index("key")


# ---------------------------------------------------------------- lineup
def player_row(name, pos, rk, board, games, week):
    k = norm_name(name)
    r = rk.loc[k] if k in rk.index else None
    team = (r["team"] if r is not None else (board.loc[k, "team"] if k in board.index else ""))
    if pos == "DST" and not team:
        # map "Seattle Seahawks" -> SEA via board
        team = board.loc[k, "team"] if k in board.index else ""
    game = games.get(team)
    bye = (r is not None and pd.notna(r["bye"]) and int(r["bye"]) == week) or (game is None)
    proj = 0.0 if bye else (float(r["proj"]) if r is not None and pd.notna(r["proj"]) else 0.0)
    return {
        "player": name, "pos": pos, "team": team, "key": k,
        "proj": proj, "ecr": (float(r["ecr"]) if r is not None and pd.notna(r["ecr"]) else None),
        "grade": (r["start_sit_grade"] if r is not None else None),
        "opp": (game[0] if game else ("BYE" if bye else "?")), "when": (game[1] if game else ""),
        "lock": (f"{game[2]} {game[3]}" if game else ""),
        "note": (r["note"] if r is not None and isinstance(r.get("note"), str) else ""),
        "tag": (r["tag"] if r is not None and isinstance(r.get("tag"), str) else ""),
        "season_proj": (float(board.loc[k, "proj_pts"]) if k in board.index else 0.0),
        "season_vbd": (float(board.loc[k, "vbd"]) if k in board.index else -50.0),
        "bye": bye,
    }


def best_lineup(players):
    """Greedy by slot is fine here: fill fixed slots with best proj, FLEX with best remaining RB/WR/TE."""
    pool = sorted(players, key=lambda p: -p["proj"])
    used, lineup = set(), []
    for slot, n in LINEUP:
        elig = [p for p in pool if p["key"] not in used and (p["pos"] in FLEX_POS if slot == "FLEX" else p["pos"] == slot)]
        for p in elig[:n]:
            lineup.append((slot, p)); used.add(p["key"])
        for _ in range(n - len(elig[:n])):
            lineup.append((slot, None))
    bench = [p for p in pool if p["key"] not in used]
    mean = sum(p["proj"] for _, p in lineup if p)
    var = sum(WEEKLY_SD.get(p["pos"], 7) ** 2 for _, p in lineup if p and p["proj"] > 0)
    return lineup, bench, mean, math.sqrt(var)


def win_prob(m1, s1, m2, s2):
    return 0.5 * (1 + math.erf((m1 - m2) / math.sqrt(s1 ** 2 + s2 ** 2) / math.sqrt(2)))


# ---------------------------------------------------------------- report
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, default=None)
    args = ap.parse_args()

    rk, scraped = load_weekly_rankings()
    sch = load_schedule()
    week = args.week or infer_week(sch)
    games = team_games(sch, week)
    rosters, matchups = load_league()
    board = load_board()

    teams = {}
    for t, plist in rosters.items():
        players = [player_row(n, pos, rk, board, games, week) for n, pos in plist]
        lineup, bench, mean, sd = best_lineup(players)
        season = sum(sorted([p["season_proj"] for p in players if p["pos"] in ("QB",)], reverse=True)[:1]) \
               + sum(sorted([p["season_proj"] for p in players if p["pos"] == "RB"], reverse=True)[:2]) \
               + sum(sorted([p["season_proj"] for p in players if p["pos"] == "WR"], reverse=True)[:2]) \
               + sum(sorted([p["season_proj"] for p in players if p["pos"] == "TE"], reverse=True)[:1])
        depth = sum(sorted([p["season_vbd"] for p in players if p["pos"] in ("RB", "WR")], reverse=True)[4:9])
        teams[t] = {"players": players, "lineup": lineup, "bench": bench, "mean": mean, "sd": sd, "season": season, "depth": depth}

    out = []
    P = out.append
    P(f"# Week {week} report — RIP EVAN LU\n")
    P(f"Expert rankings scraped {scraped}. Projections are FantasyPros weekly consensus, PPR.\n")

    me = teams[MY_TEAM_SHORT]
    opp_name = next((b if a == MY_TEAM_SHORT else a for a, b in matchups.get(week, []) if MY_TEAM_SHORT in (a, b)), None)
    opp = teams.get(opp_name) if opp_name else None

    # ---- lineup
    P(f"## 1. Your lineup to enter in ESPN\n")
    P("| Slot | Start | Opp | Game | Proj | Grade |\n|---|---|---|---|---|---|")
    for slot, p in me["lineup"]:
        if p is None: P(f"| {slot} | **EMPTY — nobody eligible** | | | | |"); continue
        P(f"| {slot} | {p['player']} | {p['opp']} | {p['when']} | {p['proj']:.1f} | {p['grade'] or ''} |")
    P(f"\nProjected total: **{me['mean']:.1f}**. Bench: " + ", ".join(f"{p['player']} ({p['proj']:.1f})" for p in me["bench"]))
    close = []
    for slot, p in me["lineup"]:
        if p is None or slot in ("K", "DST", "QB"): continue
        for b in me["bench"]:
            if b["pos"] in (FLEX_POS if slot == "FLEX" else {slot}) and b["proj"] >= p["proj"] - 1.5 and b["proj"] > 0:
                close.append(f"{p['player']} ({p['proj']:.1f}) vs {b['player']} ({b['proj']:.1f}) for {slot}: coin flip, either is fine.")
    if close: P("\nClose calls: " + " ".join(close))
    first_lock = min((p["lock"] for _, p in me["lineup"] if p and p["lock"]), default="")
    P(f"\n**Earliest lock:** {first_lock}. Set the lineup before then.\n")

    # ---- alerts
    P("## 2. Alerts\n")
    alerts = []
    for p in me["players"]:
        if p["bye"]: alerts.append(f"BYE / no game: {p['player']} ({p['pos']}) — must not be in your lineup.")
        if p["tag"] or p["note"]: alerts.append(f"{p['player']}: {p['tag']} {p['note']}".strip())
        if p["ecr"] is None: alerts.append(f"{p['player']} ({p['pos']}) is not in this week's expert rankings — likely out or irrelevant. Check ESPN.")
    if not alerts: alerts.append("No byes, no flags. ESPN injury tags (Q/D/O) still need a glance Sunday morning.")
    for a in alerts: P(f"- {a}")
    P("")

    # ---- matchup
    if opp:
        wp = win_prob(me["mean"], me["sd"], opp["mean"], opp["sd"])
        P(f"## 3. Your matchup: green fn vs {opp_name}\n")
        P(f"Projected {me['mean']:.1f} to {opp['mean']:.1f}. **Win probability {wp:.0%}.**")
        P(f"{opp_name} starters: " + ", ".join(f"{p['player']} {p['proj']:.0f}" for _, p in opp["lineup"] if p) + "\n")
        if wp < 0.42:
            P("You're the underdog: when two of your options project within a point, start the boom-or-bust one (the lower-floor, higher-ceiling player).\n")
        elif wp > 0.58:
            P("You're the favorite: when two options are close, start the steadier one.\n")

    # ---- all matchups
    P(f"## 4. Week {week} predictions\n")
    if matchups.get(week):
        P("| Matchup | Proj | Proj | Favorite | Win % |\n|---|---|---|---|---|")
        for a, b in matchups[week]:
            A, B = teams[a], teams[b]; wp = win_prob(A["mean"], A["sd"], B["mean"], B["sd"])
            fav = a if wp >= 0.5 else b
            P(f"| {a} vs {b} | {A['mean']:.1f} | {B['mean']:.1f} | **{fav}** | {max(wp, 1-wp):.0%} |")
    else:
        P("_No fantasy matchups on file for this week. Paste the ESPN League > Schedule page and I'll fill data/league_schedule.json._")
    P("")

    # ---- power rankings
    P("## 5. Power rankings\n")
    P("Season strength = projected season points of the best starting lineup (QB, 2 RB, 2 WR, TE). Depth = value of bench RB/WR 5-9. This week = projected points of this week's best lineup.\n")
    P("| # | Team | Season strength | Depth | This week |\n|---|---|---|---|---|")
    order = sorted(teams.items(), key=lambda kv: -kv[1]["season"])
    for i, (t, T) in enumerate(order, 1):
        mark = " **(you)**" if t == MY_TEAM_SHORT else ""
        P(f"| {i} | {t}{mark} | {T['season']:.0f} | {T['depth']:.0f} | {T['mean']:.1f} |")
    P(f"\nStrongest: **{order[0][0]}**. Weakest: **{order[-1][0]}**. Your rank: **{[t for t, _ in order].index(MY_TEAM_SHORT) + 1} of 12**.\n")

    # ---- waivers
    P("## 6. Waiver targets (unrostered, ranked by season value)\n")
    rostered = {norm_name(n) for pl in rosters.values() for n, _ in pl}
    fa = board[~board.index.isin(rostered) & board["pos"].isin(["QB", "RB", "WR", "TE"])].sort_values("vbd", ascending=False)
    fa = fa[[k in rk.index and pd.notna(rk.loc[k, "proj"]) and rk.loc[k, "proj"] > 0 for k in fa.index]].head(12)  # skip out/unranked
    my_worst = {pos: min((p["season_vbd"] for p in me["players"] if p["pos"] == pos), default=-99) for pos in ["RB", "WR", "TE", "QB"]}
    P("| Player | Pos | Season value | This week | Beats your worst " + "? |\n|---|---|---|---|---|")
    for k, r in fa.iterrows():
        wk = float(rk.loc[k, "proj"]) if k in rk.index and pd.notna(rk.loc[k, "proj"]) else 0.0
        better = "yes, +%.0f" % (r["vbd"] - my_worst[r["pos"]]) if r["vbd"] > my_worst[r["pos"]] else "no"
        P(f"| {r['player']} | {r['pos']} | {r['vbd']:.0f} | {wk:.1f} | {better} |")
    P("\nOnly claim someone who beats the worst player at his position on your roster; drop that player.\n")

    text = "\n".join(out)
    (ROOT / f"week{week}_report.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
