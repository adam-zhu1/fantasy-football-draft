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
