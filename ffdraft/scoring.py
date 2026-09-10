"""Convert projected stat lines into points under THIS league's scoring."""
import numpy as np
import pandas as pd


def offense_points(df: pd.DataFrame, sc: dict) -> pd.Series:
    g = lambda c: df[c].fillna(0) if c in df else 0
    return (
        g("pass_yds") * sc.get("pass_yd", 0.04)
        + g("pass_td") * sc.get("pass_td", 4)
        + g("pass_int") * sc.get("int", -2)
        + g("rush_yds") * sc.get("rush_yd", 0.1)
        + g("rush_td") * sc.get("rush_td", 6)
        + g("rec") * sc.get("rec", 1.0)
        + g("rec_yds") * sc.get("rec_yd", 0.1)
        + g("rec_td") * sc.get("rec_td", 6)
        + g("fum_lost") * sc.get("fumble_lost", -2)
    )


def td_points(df: pd.DataFrame, sc: dict) -> pd.Series:
    g = lambda c: df[c].fillna(0) if c in df else 0
    return g("pass_td") * sc.get("pass_td", 4) + g("rush_td") * sc.get("rush_td", 6) + g("rec_td") * sc.get("rec_td", 6)


def kicker_points(df: pd.DataFrame, avg_pts_per_fg: float) -> pd.Series:
    """FantasyPros gives FG made / attempted / XP only. ESPN scores FG by distance
    (3/4/5/6) and -1 per miss. avg_pts_per_fg is the historical league-average
    ESPN points per made FG, computed from nflverse."""
    fg, fga, xp = df["fg"].fillna(0), df["fga"].fillna(0), df["xpt"].fillna(0)
    return fg * avg_pts_per_fg - (fga - fg).clip(lower=0) * 1.0 + xp * 1.0


def weekly_points_from_history(h: pd.DataFrame, sc: dict) -> pd.Series:
    """League-scored weekly points from nflverse weekly stats."""
    g = lambda c: h[c].fillna(0) if c in h else 0
    fum_lost = g("rushing_fumbles_lost") + g("receiving_fumbles_lost") + g("sack_fumbles_lost")
    return (
        g("passing_yards") * sc.get("pass_yd", 0.04)
        + g("passing_tds") * sc.get("pass_td", 4)
        + g("passing_interceptions") * sc.get("int", -2)
        + g("rushing_yards") * sc.get("rush_yd", 0.1)
        + g("rushing_tds") * sc.get("rush_td", 6)
        + g("receptions") * sc.get("rec", 1.0)
        + g("receiving_yards") * sc.get("rec_yd", 0.1)
        + g("receiving_tds") * sc.get("rec_td", 6)
        + fum_lost * sc.get("fumble_lost", -2)
        + (g("passing_2pt_conversions") + g("rushing_2pt_conversions") + g("receiving_2pt_conversions")) * sc.get("two_pt", 2)
    )


def _tier(value, tiers):
    """Points for a value under [lo, hi, pts] bands. Gaps between bands score 0 (ESPN hides them)."""
    for lo, hi, pts in tiers:
        if lo <= value <= hi:
            return float(pts)
    return 0.0


def kicker_points_from_history(h: pd.DataFrame, kc: dict) -> pd.Series:
    """League-scored kicking points from nflverse weekly stats, which give FGs by distance band."""
    g = lambda c: h[c].fillna(0) if c in h else 0
    return (
        g("fg_made_0_19") * kc.get("fg_0_39", 3)
        + g("fg_made_20_29") * kc.get("fg_0_39", 3)
        + g("fg_made_30_39") * kc.get("fg_0_39", 3)
        + g("fg_made_40_49") * kc.get("fg_40_49", 4)
        + g("fg_made_50_59") * kc.get("fg_50_59", 5)
        + g("fg_made_60_") * kc.get("fg_60_plus", 6)
        + g("fg_missed") * kc.get("fg_missed", -1)
        + g("pat_made") * kc.get("pat", 1)
    )


def dst_points_from_history(t: pd.DataFrame, dc: dict) -> pd.Series:
    """League-scored team-defense points. `t` is one row per defense with the opponent's
    offensive output already joined on as points_allowed / yards_allowed."""
    g = lambda c: t[c].fillna(0) if c in t else 0
    base = (
        g("def_sacks") * dc.get("sack", 1)
        + g("def_interceptions") * dc.get("interception", 2)
        + g("fumble_recovery_opp") * dc.get("fumble_recovery", 2)
        + g("def_safeties") * dc.get("safety", 2)
        + (g("def_tds") + g("special_teams_tds")) * dc.get("return_td", 6)
        + (g("def_punt_blocks") + g("def_pat_blocks") + g("def_fg_blocks")) * dc.get("blocked_kick", 2)
        + g("def_2pt_made") * dc.get("two_pt_return", 2)
    )
    pa = t["points_allowed"].fillna(0).map(lambda v: _tier(v, dc.get("points_allowed", [])))
    ya = t["yards_allowed"].fillna(0).map(lambda v: _tier(v, dc.get("yards_allowed", [])))
    return base + pa + ya
