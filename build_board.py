#!/usr/bin/env python
"""Parts 1 + 2: load projections/ADP/history, compute floor-adjusted VBD, print tiered board, write board.csv."""
import argparse
import sys

import pandas as pd

from ffdraft.config import load_settings, ROOT
from ffdraft.history import build_priors
from ffdraft.projections import load_all_projections, load_adp
from ffdraft.vbd import build_board

pd.set_option("display.width", 200)


def fmt_row(r):
    tag = ("C" if r.committee else " ") + ("T" if r.td_dep else " ")
    adp = f"{r.adp_avg:5.1f}" if pd.notna(r.adp_avg) else "  n/a"
    bye = f"{int(r.bye):2d}" if pd.notna(r.bye) else " ?"
    mr = f"{int(r.market_rank):3d}" if pd.notna(r.market_rank) else "  -"
    return (f"{int(r['rank']):3d}  {r.pos:<3}{int(r.pos_rank):<3d} T{int(r.pos_tier):<2d} "
            f"{r.player[:24]:<24} {r.team:<4}bye{bye}  proj{r.proj_pts:6.1f}  g{r.exp_games:4.1f}  sd{r.weekly_sd:4.1f}  "
            f"VBD{r.vbd:6.1f} (model{r.vbd_model:6.1f}) exp#{mr} ADP{adp} {tag}")


def print_overall(df, n):
    print(f"\n=== OVERALL BOARD (top {n}, K/DST listed separately — never before the last two rounds) ===")
    print("     pos     tier  player                   team bye   proj    exp_g  wk_sd   floor     VBD     ADP  flags(C=committee,T=TD-dependent)")
    for _, r in df[~df["pos"].isin(["K", "DST"])].head(n).iterrows():
        print(fmt_row(r))


def print_positional(df, pos, n):
    sub = df[df["pos"] == pos].head(n)
    print(f"\n=== {pos} (baseline {pos}{df.attrs['baselines'][pos][0]} = {df.attrs['baselines'][pos][1]} floor pts) ===")
    last_t = None
    for _, r in sub.iterrows():
        if last_t is not None and r.pos_tier != last_t:
            print("     " + "-" * 60 + f"  tier {int(r.pos_tier)}")
        last_t = r.pos_tier
        print(fmt_row(r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=120)
    ap.add_argument("--risk", type=float, default=None, help="override risk_aversion")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    s = load_settings()
    if args.risk is not None:
        s["risk_aversion"] = args.risk

    proj = load_all_projections()
    adp = load_adp()
    priors = build_priors(s)
    board = build_board(proj, adp, priors, s)

    cols = ["rank", "player", "team", "pos", "pos_rank", "pos_tier", "bye", "proj_pts", "ppg", "exp_games", "weekly_sd",
            "floor_value", "vbd", "vbd_model", "vbd_market", "market_rank", "model_rank", "avoid", "vbd_raw", "committee", "td_share", "td_dep", "adp_avg", "adp_espn", "adp_sleeper", "adp_diff",
            "fp_pts", "hist_seasons", "hist_games", "key"]
    board[cols].to_csv(ROOT / "board.csv", index=False)

    if args.quiet:
        return
    print(f"settings: {s['num_teams']} teams, scoring={s['scoring']}, risk_aversion={s['risk_aversion']}, market_weight={s['market_weight']} (expert/ADP rank share)")
    print("baselines (N, floor pts):", board.attrs["baselines"])
    pg = priors["pos_prior_games"]
    print("positional expected games (last 3 seasons, relevant players):", {p: round(v, 1) for p, v in pg.items()})
    print("weekly SD by position/tier:")
    print(priors["tier_sd"].pivot(index="position", columns="tier", values="weekly_sd").round(1).to_string())
    print(f"ESPN pts per made FG (historical): {priors['avg_pts_per_fg']:.2f}")
    unmatched = board[board["adp_avg"].isna() & (board["proj_pts"] > 60)]
    if len(unmatched):
        print(f"\n[warn] {len(unmatched)} projected players (>60 pts) with no ADP match:", ", ".join(unmatched["player"].head(15)))

    print_overall(board, args.top)
    for pos, n in [("RB", 45), ("WR", 55), ("QB", 20), ("TE", 18), ("K", 14), ("DST", 14)]:
        print_positional(board, pos, n)
    print(f"\nwrote {ROOT / 'board.csv'} ({len(board)} players)")


if __name__ == "__main__":
    main()
