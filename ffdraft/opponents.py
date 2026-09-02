"""Opponent modeling: simulate the picks between now and my next turn using each team's roster.

Bot (autodraft) teams take the best remaining player by ESPN rank, respecting basic roster limits.
Human teams draw from a distribution centered on ADP (steeply decaying), scaled by positional need.
Returns per-player survival probability and, per position, the expected best VBD left at my pick
(VONA: value over next available).
"""
import numpy as np
import pandas as pd

TAU = 3.5           # humans: weight = exp(-i / TAU) for the i-th best available by ADP
N_SIMS = 400
CANDIDATES = 110    # only the top-N by ADP can realistically be taken in the next couple of rounds


def need_multiplier(pos, counts, rnd, lineup, is_bot=False):
    have = counts.get(pos, 0)
    if pos in ("K", "DST"):
        if is_bot:
            return 1.0 if have == 0 else 0.0
        if have >= 1: return 0.0
        return 0.02 if rnd < 13 else (4.0 if rnd >= 15 else 1.0)
    if pos in ("QB", "TE"):
        if have >= 1: return 0.0 if is_bot and rnd < 13 else 0.12
        return 1.0 if rnd < 10 else 2.0
    # RB / WR
    starters = lineup.get(pos, 2)
    if have < starters: return 1.4
    if have >= 6: return 0.35
    return 1.0


def simulate(avail: pd.DataFrame, picks_between, rosters, bots, lineup, my_counts=None, n_sims=N_SIMS, seed=0):
    """
    avail: DataFrame of available players (index=key) with adp_avg, adp_espn, vbd, pos
    picks_between: list of (pick_no, slot) that happen before my pick
    rosters: {slot: {pos: count}}
    bots: set of slots that autodraft
    Returns (survival: dict key->prob, exp_best_next: dict pos->expected best VBD left, taken_freq: dict key->freq)
    """
    if len(avail) == 0:
        return {}, {}, {}
    a = avail.copy()
    a["_adp"] = a["adp_avg"].fillna(400.0)
    a["_espn"] = a["adp_espn"].fillna(a["_adp"] + 30)
    a = a.sort_values("_adp").head(CANDIDATES)
    keys = a.index.to_numpy()
    pos = a["pos"].to_numpy()
    vbd = a["vbd"].to_numpy(dtype=float)
    adp_order = np.arange(len(a))                     # already sorted by adp
    espn_rank = a["_espn"].to_numpy(dtype=float)
    n = len(a)
    rng = np.random.default_rng(seed)
    positions = ["QB", "RB", "WR", "TE", "K", "DST"]
    pos_idx = {p: (pos == p) for p in positions}

    # the whole pool (beyond candidates) for "best remaining" per position
    rest_best = {p: (avail[(avail["pos"] == p) & (~avail.index.isin(keys))]["vbd"].max() if ((avail["pos"] == p) & (~avail.index.isin(keys))).any() else np.nan) for p in positions}

    survived = np.zeros(n)
    best_next = {p: [] for p in positions}

    if not picks_between:
        for p in positions:
            m = pos_idx[p]
            best_next[p].append(np.nanmax(np.concatenate([vbd[m], [rest_best[p]]])) if m.any() or not np.isnan(rest_best[p]) else np.nan)
        return {k: 1.0 for k in keys}, {p: (float(np.nanmean(v)) if len(v) else np.nan) for p, v in best_next.items()}, {k: 0.0 for k in keys}

    for _ in range(n_sims):
        alive = np.ones(n, dtype=bool)
        counts = {s: dict(c) for s, c in rosters.items()}
        for pick_no, slot in picks_between:
            rnd = (pick_no - 1) // 12 + 1
            c = counts.setdefault(slot, {})
            mult = np.zeros(n)
            is_bot = slot in bots
            for p in positions:
                m = need_multiplier(p, c, rnd, lineup, is_bot)
                if m > 0:
                    mult[pos_idx[p]] = m
            w = mult * alive
            if is_bot:
                # deterministic: best ESPN rank among allowed
                cand = np.where(w > 0)[0]
                if len(cand) == 0:
                    cand = np.where(alive)[0]
                if len(cand) == 0:
                    continue
                j = cand[np.argmin(espn_rank[cand])]
            else:
                # rank among *alive* players by ADP, then exp decay
                alive_idx = np.where(alive)[0]
                if len(alive_idx) == 0:
                    continue
                order_rank = np.empty(n); order_rank[:] = 1e9
                order_rank[alive_idx] = np.arange(len(alive_idx))       # alive_idx is in adp order already
                ww = np.exp(-order_rank / TAU) * w
                if ww.sum() <= 0:
                    ww = np.exp(-order_rank / TAU) * alive
                ww = ww / ww.sum()
                j = rng.choice(n, p=ww)
            alive[j] = False
            c[pos[j]] = c.get(pos[j], 0) + 1
        survived += alive
        for p in positions:
            m = pos_idx[p] & alive
            cands = vbd[m]
            rb = rest_best[p]
            if len(cands):
                best_next[p].append(max(cands.max(), rb) if not np.isnan(rb) else cands.max())
            elif not np.isnan(rb):
                best_next[p].append(rb)

    surv = survived / n_sims
    survival = {k: float(s) for k, s in zip(keys, surv)}
    exp_best = {p: (float(np.mean(v)) if len(v) else np.nan) for p, v in best_next.items()}
    return survival, exp_best, {k: float(1 - s) for k, s in zip(keys, surv)}
