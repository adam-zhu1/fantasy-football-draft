"""Historical priors from nflverse (via nflreadpy):
  - expected games played per position, and per-player games history
  - weekly SD of fantasy points by (position, tier of 12)
  - ESPN points per made FG (distance-weighted)
Cached in data/cache so the download happens once."""
import numpy as np
import pandas as pd

from .config import CACHE
from .names import norm_name
from .scoring import weekly_points_from_history

OFF_POS = ["QB", "RB", "WR", "TE"]
# "relevant" pool per season for priors = top N by points-per-game (avoids selecting on games played)
POOL = {"QB": 18, "RB": 40, "WR": 50, "TE": 18}
POOL_MIN_GAMES = 6
TIER_SIZE = 12


def load_weekly(seasons):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"weekly_{'_'.join(map(str, seasons))}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    import nflreadpy as nfl
    pl_df = nfl.load_player_stats(seasons=list(seasons))
    df = pl_df.to_pandas()
    df = df[(df["season_type"] == "REG")]
    df.to_parquet(f, index=False)
    return df


def build_priors(settings):
    seasons = settings["history_seasons"]
    sc = settings["scoring_detail"]
    G = settings["games_in_season"]
    w = load_weekly(seasons)

    # ---- kicker: ESPN pts per made FG ----
    k = w[w["position"] == "K"]
    fg_pts = (3 * (k["fg_made_0_19"].fillna(0) + k["fg_made_20_29"].fillna(0) + k["fg_made_30_39"].fillna(0))
              + 4 * k["fg_made_40_49"].fillna(0) + 5 * k["fg_made_50_59"].fillna(0) + 6 * k["fg_made_60_"].fillna(0))
    avg_pts_per_fg = float(fg_pts.sum() / max(k["fg_made"].fillna(0).sum(), 1))

    # ---- offense weekly points under league scoring ----
    o = w[w["position"].isin(OFF_POS)].copy()
    o["pts"] = weekly_points_from_history(o, sc)
    o["key"] = o["player_display_name"].map(norm_name)

    # player-season aggregates
    ps = (o.groupby(["player_id", "key", "player_display_name", "position", "season"])
            .agg(games=("week", "nunique"), total=("pts", "sum"), sd=("pts", "std"))
            .reset_index())
    ps["ppg"] = ps["total"] / ps["games"]

    # positional rank within season by TOTAL points (this is how "tiers" are experienced in a draft)
    ps["pos_rank"] = ps.groupby(["season", "position"])["total"].rank(ascending=False, method="first")
    ps["tier"] = np.ceil(ps["pos_rank"] / TIER_SIZE).astype(int)

    # ---- weekly SD by (position, tier), pooled across seasons; need >= 6 games ----
    ok = ps[ps["games"] >= 6]
    tier_sd = (ok.groupby(["position", "tier"])["sd"].median().rename("weekly_sd").reset_index())
    tier_sd = tier_sd[tier_sd["tier"] <= 6]

    # ---- games-played prior by position, from pool selected on ppg ----
    elig = ps[ps["games"] >= POOL_MIN_GAMES].copy()
    elig["ppg_rank"] = elig.groupby(["season", "position"])["ppg"].rank(ascending=False, method="first")
    pool = elig[elig.apply(lambda r: r["ppg_rank"] <= POOL[r["position"]], axis=1)]
    pos_games = pool.groupby("position")["games"].mean().rename("prior_games")
    pos_games = (pos_games.clip(upper=G))

    # ---- per-player games history (all seasons they appeared) ----
    player_hist = (ps.groupby(["key", "position"])
                     .agg(hist_seasons=("season", "nunique"), hist_games=("games", "sum"))
                     .reset_index())
    player_hist["hist_rate"] = player_hist["hist_games"] / (player_hist["hist_seasons"] * G)

    return {
        "avg_pts_per_fg": avg_pts_per_fg,
        "tier_sd": tier_sd,
        "pos_prior_games": pos_games,
        "player_hist": player_hist,
        "player_seasons": ps,
    }
