"""Value-based drafting with floor adjustment.

floor_value = exp_games * (ppg - RISK_AVERSION * weekly_sd)   [per-game floor, then times games]
             * (1 - committee_pen) * (1 - td_pen)
VBD = floor_value - baseline_floor_value(position)
"""
import numpy as np
import pandas as pd

from .config import DATA
from .scoring import offense_points, td_points, kicker_points


def baselines_from_settings(s):
    t = s["num_teams"]
    L = s["starting_lineup"]
    flex_total = t * L.get("FLEX", 0)
    fs = s["flex_share"]
    b = {
        "QB": t * (L.get("QB", 0) + L.get("SUPERFLEX", 0)),
        "RB": t * L.get("RB", 0) + round(flex_total * fs.get("RB", 0.5)),
        "WR": t * L.get("WR", 0) + round(flex_total * fs.get("WR", 1.0)),
        "TE": t * L.get("TE", 0),
        "K": t * L.get("K", 0),
        "DST": t * L.get("DEF", 0),
    }
    return {k: int(v) for k, v in b.items() if v > 0}


def committee_flags(df):
    """Data-driven committee flag for RBs: another RB on the same team projects >=110 carries
    AND >=60% of this player's carries. Manual overrides in data/flags.csv (player,committee,note)."""
    rb = df[df["pos"] == "RB"][["player", "team", "rush_att"]].copy()
    flag = pd.Series(False, index=df.index)
    for team, grp in rb.groupby("team"):
        if len(grp) < 2:
            continue
        for i, r in grp.iterrows():
            others = grp.drop(i)
            if ((others["rush_att"] >= 110) & (others["rush_att"] >= 0.60 * r["rush_att"])).any():
                flag[i] = True
    # manual overrides
    f = DATA / "flags.csv"
    if f.exists():
        man = pd.read_csv(f, dtype=str, keep_default_na=False)
        from .names import norm_name
        man["key"] = man["player"].map(norm_name)
        m = dict(zip(man["key"], man["committee"].str.strip()))
        for i, k in df["key"].items():
            if k in m and m[k] in ("0", "1"):
                flag[i] = m[k] == "1"
    return flag


def build_board(proj: pd.DataFrame, adp: pd.DataFrame, priors: dict, s: dict) -> pd.DataFrame:
    sc = s["scoring_detail"]
    G = s["games_in_season"]
    RA = s["risk_aversion"]
    df = proj.copy()

    # ---- league-scored season projection ----
    off = df["pos"].isin(["QB", "RB", "WR", "TE"])
    df.loc[off, "proj_pts"] = offense_points(df[off], sc)
    df.loc[off, "td_pts"] = td_points(df[off], sc)
    kk = df["pos"] == "K"
    df.loc[kk, "proj_pts"] = kicker_points(df[kk], priors["avg_pts_per_fg"])
    dd = df["pos"] == "DST"
    df.loc[dd, "proj_pts"] = df.loc[dd, "fp_pts"]
    df["ppg"] = df["proj_pts"] / G

    # ---- ADP join (offense by name; DST by team nickname) ----
    a = adp.copy()
    a = a.sort_values("adp_avg").drop_duplicates("key")
    df = df.merge(a[["key", "bye", "adp_avg", "adp_espn", "adp_sleeper"]], on="key", how="left")
    # bye for players missing from ADP: borrow team bye
    team_bye = df.dropna(subset=["bye"]).groupby("team")["bye"].agg(lambda x: x.mode().iloc[0])
    df["bye"] = df["bye"].fillna(df["team"].map(team_bye))

    # ---- expected games: blend player history with positional prior ----
    ph = priors["player_hist"].rename(columns={"position": "pos"})
    df = df.merge(ph[["key", "pos", "hist_seasons", "hist_games", "hist_rate"]], on=["key", "pos"], how="left")
    prior_games = priors["pos_prior_games"]
    k = s["prior_shrink_seasons"]
    def exp_games(r):
        if r["pos"] in ("K", "DST"):
            return G
        pg = prior_games.get(r["pos"], G - 1.5)
        if pd.isna(r["hist_seasons"]):
            return pg
        n = r["hist_seasons"]
        return (n * r["hist_rate"] * G + k * pg) / (n + k)
    df["exp_games"] = df.apply(exp_games, axis=1).clip(upper=G)

    # ---- weekly SD by projected tier ----
    df["pos_rank_proj"] = df.groupby("pos")["proj_pts"].rank(ascending=False, method="first")
    df["tier12"] = np.ceil(df["pos_rank_proj"] / 12).clip(upper=6).astype(int)
    tsd = priors["tier_sd"].rename(columns={"position": "pos", "tier": "tier12"})
    df = df.merge(tsd, on=["pos", "tier12"], how="left")
    df["weekly_sd"] = df["weekly_sd"].fillna(0.0)

    # ---- floor value ----
    df["floor_ppg"] = df["ppg"] - RA * df["weekly_sd"]
    df["floor_value"] = df["exp_games"] * df["floor_ppg"]

    # penalties
    df["committee"] = committee_flags(df)
    skill = df["pos"].isin(["RB", "WR", "TE"])
    df["td_share"] = (df["td_pts"] / df["proj_pts"]).where(skill, 0).fillna(0)
    df["td_dep"] = df["td_share"] > s["td_dependence_threshold"]
    mult = (1 - s["committee_penalty"] * df["committee"]) * (1 - s["td_dependence_penalty"] * df["td_dep"])
    df["floor_value"] = df["floor_value"] * mult

    # ---- baselines & VBD ----
    base_n = baselines_from_settings(s)
    base_pts, base_raw = {}, {}
    for pos, n in base_n.items():
        col = df[df["pos"] == pos].sort_values("floor_value", ascending=False)
        base_pts[pos] = col["floor_value"].iloc[min(n, len(col)) - 1]
        colr = df[df["pos"] == pos].sort_values("proj_pts", ascending=False)
        base_raw[pos] = colr["proj_pts"].iloc[min(n, len(colr)) - 1]
    df["baseline_n"] = df["pos"].map(base_n)
    df["vbd"] = df["floor_value"] - df["pos"].map(base_pts)
    df["vbd_raw"] = df["proj_pts"] - df["pos"].map(base_raw)

    df = df.sort_values("vbd", ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    df["pos_rank"] = df.groupby("pos")["vbd"].rank(ascending=False, method="first").astype(int)
    df["pos_tier"] = tiers_by_gap(df)
    df["adp_diff"] = df["adp_avg"] - df["rank"]      # + means we like him more than the market
    df.attrs["baselines"] = {p: (base_n[p], round(base_pts[p], 1)) for p in base_n}
    return df


def tiers_by_gap(df, min_gap=None):
    """Within each position: a new tier starts when either (a) the VBD drop to the next player
    exceeds GAP, or (b) the spread of the current tier would exceed WIDTH. Keeps tiers tight
    at the top (big gaps) and stops mid-board tiers from sprawling."""
    WIDTH = {"QB": 14, "RB": 13, "WR": 13, "TE": 12, "K": 6, "DST": 6}
    GAP = {"QB": 9, "RB": 9, "WR": 9, "TE": 8, "K": 4, "DST": 4}
    out = pd.Series(1, index=df.index)
    for pos, grp in df.groupby("pos", sort=False):
        g = grp.sort_values("vbd", ascending=False)
        v = g["vbd"].to_numpy()
        w, gp = WIDTH.get(pos, 12), GAP.get(pos, 8)
        t, tiers, start = 1, [1], v[0] if len(v) else 0
        for i in range(1, len(v)):
            if (v[i - 1] - v[i]) > gp or (start - v[i]) > w:
                t += 1
                start = v[i]
            tiers.append(t)
        out[g.index] = tiers
    return out
