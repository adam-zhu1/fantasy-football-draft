"""Parse FantasyPros projection + ADP exports into one tidy DataFrame."""
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DATA
from .names import norm_name, norm_team


def _read(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df[df.iloc[:, 0].str.strip() != ""]          # FantasyPros inserts a blank row
    return df.reset_index(drop=True)


def _num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.strip(), errors="coerce")


def _find(pattern):
    hits = sorted(DATA.glob(pattern))
    if not hits:
        raise FileNotFoundError(f"No file in data/ matching {pattern}")
    return hits[-1]


def load_flex():
    """RB/WR/TE. Columns (positional, names repeat): Player,Team,POS,ATT,YDS,TDS,REC,YDS,TDS,FL,FPTS"""
    df = _read(_find("*Projections_FLX*.csv"))
    df.columns = ["player", "team", "pos_rank", "rush_att", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td", "fum_lost", "fp_pts"]
    df["pos"] = df["pos_rank"].str.extract(r"([A-Z]+)")[0]
    for c in ["rush_att", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td", "fum_lost", "fp_pts"]:
        df[c] = _num(df[c])
    return df.drop(columns="pos_rank")


def load_qb():
    """Columns: Player,Team,ATT,CMP,YDS,TDS,INTS,ATT,YDS,TDS,FL,FPTS"""
    df = _read(_find("*Projections_QB*.csv"))
    df.columns = ["player", "team", "pass_att", "pass_cmp", "pass_yds", "pass_td", "pass_int", "rush_att", "rush_yds", "rush_td", "fum_lost", "fp_pts"]
    for c in df.columns[2:]:
        df[c] = _num(df[c])
    df["pos"] = "QB"
    return df


def load_k():
    df = _read(_find("*Projections_K*.csv"))
    df.columns = ["player", "team", "fg", "fga", "xpt", "fp_pts"]
    for c in df.columns[2:]:
        df[c] = _num(df[c])
    df["pos"] = "K"
    return df


def load_dst():
    df = _read(_find("*Projections_DST*.csv"))
    df = df.rename(columns={"Player": "player", "Team": "team", "FPTS": "fp_pts"})
    df["fp_pts"] = _num(df["fp_pts"])
    df["pos"] = "DST"
    return df[["player", "team", "fp_pts", "pos"]]


def load_adp():
    """Rank,Player (Bye),POS,ESPN,Sleeper,...,AVG,Real-Time"""
    df = _read(_find("*ADP*.csv"))
    pb = df["Player (Bye)"].str.strip()
    # "Jahmyr Gibbs   DET (6)"  |  "Houston Texans   (6)"  | some rows may lack bye
    m = pb.str.extract(r"^(?P<name>.+?)\s{2,}(?P<team>[A-Z]{2,3})?\s*(?:\((?P<bye>\d+)\))?$")
    out = pd.DataFrame({
        "player": m["name"].fillna(pb).str.strip().str.replace(r"\s+DST$", "", regex=True),
        "adp_team": m["team"].fillna("").map(norm_team),
        "bye": pd.to_numeric(m["bye"], errors="coerce"),
        "pos": df["POS"].str.extract(r"([A-Z]+)")[0],
        "adp_avg": _num(df["AVG"]),
        "adp_espn": _num(df["ESPN"]) if "ESPN" in df else np.nan,
        "adp_sleeper": _num(df["Sleeper"]) if "Sleeper" in df else np.nan,
    })
    out["key"] = out["player"].map(norm_name)
    return out


def load_all_projections():
    parts = [load_flex(), load_qb(), load_k(), load_dst()]
    df = pd.concat(parts, ignore_index=True, sort=False)
    df["player"] = df["player"].str.strip()
    df["team"] = df["team"].map(norm_team)
    df["key"] = df["player"].map(norm_name)
    # DST: key on team nickname so ADP ("Houston Texans") joins
    return df
