#!/usr/bin/env python
"""Backtest the draft strategy on a past season.

Setup (no hindsight):
  - Preseason market = FantasyFootballCalculator 12-team PPR ADP for that year (drafts in the last week of August).
  - Projection proxy = ADP positional rank mapped onto a stable positional points curve (from the current board),
    since historical consensus projections aren't freely available. So every strategy starts from the SAME
    ordinal ranking; what differs is the decision layer on top of it.
  - Floor adjustment uses only the three seasons BEFORE the backtest year.
Evaluation:
  - 12-team snake draft, 16 rounds (rounds 15-16 = K/DST placeholders, not scored). Opponents draft from an
    ADP-centred distribution with positional-need logic. My slot is random each draft.
  - Every roster is scored on the actual season: weekly best lineup (QB, 2RB, 2WR, TE, FLEX) weeks 1-14,
    players with no stat line that week score 0 (bye / injury / benched).
  - Metrics: my season total, my rank among the 12 (by total points), P(last), P(bottom 3), 10th percentile.
"""
import argparse, json, math, sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ffdraft.config import load_settings, DATA, CACHE, ROOT
from ffdraft.history import build_priors, load_weekly
from ffdraft.names import norm_name
from ffdraft.scoring import weekly_points_from_history
from ffdraft.opponents import need_multiplier

T, ROUNDS, OFF_ROUNDS = 12, 16, 14
LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
MIN_RB, MIN_WR = 5, 6
TAU = 3.5
WEEKS = range(1, 15)


def ffc_adp(year):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"ffc_adp_ppr_{year}.json"
    if not f.exists():
        r = requests.get(f"https://fantasyfootballcalculator.com/api/v1/adp/ppr?teams=12&year={year}", timeout=30)
        r.raise_for_status(); f.write_text(r.text)
    d = json.loads(f.read_text())["players"]
    df = pd.DataFrame(d)[["name", "position", "team", "adp", "bye"]].rename(columns={"name": "player", "position": "pos"})
    df = df[df["pos"].isin(["QB", "RB", "WR", "TE"])].copy()
    df["key"] = df["player"].map(norm_name)
    df = df.sort_values("adp").drop_duplicates("key").reset_index(drop=True)
    return df


def points_curve():
    """Positional points curve from the current FantasyPros board: pos -> sorted projected points."""
    b = pd.read_csv(ROOT / "board.csv")
    return {p: np.sort(g["proj_pts"].to_numpy())[::-1] for p, g in b.groupby("pos") if p in LINEUP or p == "TE"}


def proxy_projection(adp, curve):
    adp = adp.copy()
    adp["pos_rank"] = adp.groupby("pos")["adp"].rank(method="first").astype(int)
    def pts(r):
        c = curve[r["pos"]]; i = r["pos_rank"] - 1
        return float(c[i]) if i < len(c) else float(c[-1] * (0.93 ** (i - len(c) + 1)))
    adp["proj_pts"] = adp.apply(pts, axis=1)
    return adp


def floor_adjust(proj, year, s):
    """Apply the engine's floor model using only seasons before `year`."""
    s = dict(s); s["history_seasons"] = [year - 3, year - 2, year - 1]
    pri = build_priors(s)
    G = s["games_in_season"]; RA = s["risk_aversion"]; k = s["prior_shrink_seasons"]
    df = proj.copy()
    df["ppg"] = df["proj_pts"] / G
    ph = pri["player_hist"].rename(columns={"position": "pos"})
    df = df.merge(ph[["key", "pos", "hist_seasons", "hist_rate"]], on=["key", "pos"], how="left")
    pg = pri["pos_prior_games"]
    def eg(r):
        p = pg.get(r["pos"], G - 2)
        if pd.isna(r["hist_seasons"]): return p
        return (r["hist_seasons"] * r["hist_rate"] * G + k * p) / (r["hist_seasons"] + k)
    df["exp_games"] = df.apply(eg, axis=1).clip(upper=G)
    df["tier12"] = np.ceil(df["pos_rank"] / 12).clip(upper=6).astype(int)
    tsd = pri["tier_sd"].rename(columns={"position": "pos", "tier": "tier12"})
    df = df.merge(tsd, on=["pos", "tier12"], how="left"); df["weekly_sd"] = df["weekly_sd"].fillna(0)
    df["floor_value"] = df["exp_games"] * (df["ppg"] - RA * df["weekly_sd"])
    return df


def add_vbd(df, col, out):
    base_n = {"QB": 12, "RB": 30, "WR": 36, "TE": 12}
    for p, n in base_n.items():
        v = df.loc[df["pos"] == p, col].sort_values(ascending=False)
        b = v.iloc[min(n, len(v)) - 1]
        df.loc[df["pos"] == p, out] = df.loc[df["pos"] == p, col] - b
    return df


def actual_weekly(year, s):
    w = load_weekly([year])
    w = w[w["position"].isin(["QB", "RB", "WR", "TE"]) & w["week"].isin(list(WEEKS))].copy()
    w["pts"] = weekly_points_from_history(w, s["scoring_detail"])
    w["key"] = w["player_display_name"].map(norm_name)
    piv = w.pivot_table(index="key", columns="week", values="pts", aggfunc="sum").reindex(columns=list(WEEKS)).fillna(0.0)
    return piv


def season_total(roster_keys, roster_pos, piv):
    """Weekly optimal lineup: QB1 RB2 WR2 TE1 FLEX1."""
    total = 0.0
    keys = [k for k in roster_keys if k in piv.index]
    if not keys: return 0.0
    pts = piv.loc[keys].to_numpy()  # players x weeks
    pos = np.array([roster_pos[k] for k in keys])
    for wi in range(pts.shape[1]):
        col = pts[:, wi]
        used = np.zeros(len(keys), bool); wk = 0.0
        for p, n in [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1)]:
            idx = np.where((pos == p) & ~used)[0]
            best = idx[np.argsort(-col[idx])][:n]
            wk += col[best].sum(); used[best] = True
        idx = np.where(np.isin(pos, ["RB", "WR", "TE"]) & ~used)[0]
        if len(idx): wk += col[idx].max()
        total += wk
    return total


# ---------------------------------------------------------------- draft strategies
def hard_ok(pos, counts, picks_left, rnd):
    if pos in ("K", "DST"): return False
    if pos in ("QB", "TE") and counts.get(pos, 0) >= 1: return False
    if counts.get(pos, 0) >= 8: return False
    missing = int(counts.get("QB", 0) == 0) + int(counts.get("TE", 0) == 0)
    need_rb = max(0, MIN_RB - counts.get("RB", 0) - (pos == "RB"))
    need_wr = max(0, MIN_WR - counts.get("WR", 0) - (pos == "WR"))
    return picks_left - 1 >= missing + need_rb + need_wr


def need_bonus(pos, counts, rnd):
    rb, wr = counts.get("RB", 0), counts.get("WR", 0)
    flex_open = rb + wr < 5
    if pos in ("RB", "WR"):
        have, mn, st = (rb, MIN_RB, 2) if pos == "RB" else (wr, MIN_WR, 2)
        if have < st: return 12
        if flex_open: return 6
        if have < mn: return 4
        return -8
    if pos in ("QB", "TE"):
        return 15 if rnd >= 10 else (6 if rnd >= 7 else 0)
    return 0


def my_pick(strategy, avail, counts, picks_left, rnd):
    a = avail
    if strategy == "adp_naive":
        # take best ADP; only rules: max 2 QB / 2 TE
        for _, r in a.sort_values("adp").iterrows():
            if r["pos"] in ("QB", "TE") and counts.get(r["pos"], 0) >= 2: continue
            return r["key"]
    ok = a[[hard_ok(p, counts, picks_left, rnd) for p in a["pos"]]]
    if ok.empty: ok = a
    if strategy == "adp_rules":
        return ok.sort_values("adp").iloc[0]["key"]
    col = {"raw_vbd": "vbd_raw", "floor_vbd": "vbd_floor"}[strategy]
    sc = ok[col] + np.array([need_bonus(p, counts, rnd) for p in ok["pos"]])
    return ok.loc[sc.idxmax(), "key"]


def opp_pick(avail, counts, rnd, rng):
    a = avail.sort_values("adp")
    mult = np.array([need_multiplier(p, counts, rnd, {"RB": 2, "WR": 2}) for p in a["pos"]])
    w = np.exp(-np.arange(len(a)) / TAU) * mult
    if w.sum() <= 0: w = np.exp(-np.arange(len(a)) / TAU)
    w /= w.sum()
    return a.iloc[rng.choice(len(a), p=w)]["key"]


def run_draft(players, strategy, my_slot, rng, bots=frozenset()):
    avail = players.copy()
    rosters = {s: [] for s in range(1, T + 1)}
    counts = {s: {} for s in range(1, T + 1)}
    for pick in range(1, OFF_ROUNDS * T + 1):
        rnd = (pick - 1) // T + 1
        i = (pick - 1) % T
        slot = i + 1 if rnd % 2 == 1 else T - i
        if slot == my_slot:
            picks_left = OFF_ROUNDS - rnd + 1
            key = my_pick(strategy, avail, counts[slot], picks_left, rnd)
        elif slot in bots:
            a = avail[[hard_ok(p, counts[slot], OFF_ROUNDS - rnd + 1, rnd) or p in ("RB", "WR") for p in avail["pos"]]]
            key = (a if len(a) else avail).sort_values("adp").iloc[0]["key"]
        else:
            key = opp_pick(avail, counts[slot], rnd, rng)
        pos = avail.loc[key, "pos"]
        rosters[slot].append(key); counts[slot][pos] = counts[slot].get(pos, 0) + 1
        avail = avail.drop(key)
    return rosters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--risk", type=float, default=None)
    ap.add_argument("--slot", type=int, default=None, help="fix my draft slot (default random)")
    ap.add_argument("--strategies", default="adp_naive,adp_rules,raw_vbd,floor_vbd")
    ap.add_argument("--bots", default="", help="comma-separated slots that autodraft by ADP")
    args = ap.parse_args()
    s = load_settings()
    if args.risk is not None: s["risk_aversion"] = args.risk

    adp = ffc_adp(args.year)
    proj = proxy_projection(adp, points_curve())
    df = floor_adjust(proj, args.year, s)
    df = add_vbd(df, "proj_pts", "vbd_raw")
    df = add_vbd(df, "floor_value", "vbd_floor")
    df = df.set_index("key", drop=False)
    piv = actual_weekly(args.year, s)
    matched = df["key"].isin(piv.index).mean()
    print(f"{args.year}: {len(df)} drafted-pool players from ADP; {matched:.0%} matched to actual stats; risk_aversion={s['risk_aversion']}")
    pos_of = df["pos"].to_dict()

    strategies = args.strategies.split(",")
    bots = {int(x) for x in args.bots.split(",") if x}
    results = {st: [] for st in strategies}
    for d in range(args.n):
        slot = args.slot or int(np.random.default_rng(1000 + d).integers(1, T + 1))
        for st in strategies:
            rng = np.random.default_rng(d)   # same opponent randomness for each strategy
            rosters = run_draft(df, st, slot, rng, bots)
            totals = {sl: season_total(r, pos_of, piv) for sl, r in rosters.items()}
            mine = totals[slot]
            rank = 1 + sum(1 for sl, t in totals.items() if sl != slot and t > mine)
            results[st].append((mine, rank, slot))
        if (d + 1) % 25 == 0: print(f"  {d+1}/{args.n} drafts", file=sys.stderr)

    print(f"\n{'strategy':<12}{'mean pts':>10}{'10th pct':>10}{'median':>9}{'mean rank':>11}{'P(last)':>9}{'P(bot3)':>9}{'P(top4)':>9}")
    for st in strategies:
        a = np.array(results[st]); pts, rk = a[:, 0], a[:, 1]
        print(f"{st:<12}{pts.mean():>10.0f}{np.percentile(pts, 10):>10.0f}{np.median(pts):>9.0f}{rk.mean():>11.2f}{(rk == 12).mean():>9.1%}{(rk >= 10).mean():>9.1%}{(rk <= 4).mean():>9.1%}")
    out = DATA / "cache" / f"backtest_{args.year}_slot{args.slot or 0}.json"
    out.write_text(json.dumps({st: results[st] for st in strategies}))


if __name__ == "__main__":
    main()
