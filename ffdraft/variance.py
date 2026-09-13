"""Per-player weekly scoring distributions, fitted from 2023-25 history.

What this replaces: a single hardcoded standard deviation per position (QB 7.5, RB 7.5,
WR 8.0, TE 6.0, K 4.0, DST 6.0) and a normal-curve win probability. That treated every
quarterback as equally unpredictable and ignored the fact that fantasy scoring is
right-skewed, because a touchdown is six points arriving all at once.

What it does instead, per position:

  1. Fits spread as a straight line in scoring level, sd = a + b * ppg, on player-seasons
     with at least 6 games. Bigger players really do swing harder in absolute terms.
  2. Scales that by the player's own history. A player whose weekly scores bounced around
     more than the curve predicted keeps some of that, shrunk toward the curve by how many
     games he has. Shrinkage means a rookie or a player with 3 games ends up near the
     positional curve rather than at whatever his tiny sample says.
  3. Draws from a pool of standardised historical residuals rather than a bell curve, so
     simulated weeks keep the real shape: most below average, a few enormous.

Everything is fitted once and cached to data/cache/variance.json.
"""
import json
import math

import numpy as np
import pandas as pd

from .config import CACHE, load_settings
from .names import norm_name
from .scoring import weekly_points_from_history, kicker_points_from_history, dst_points_from_history

S = load_settings()
OFF_POS = ["QB", "RB", "WR", "TE"]
MIN_GAMES_FIT = 6          # player-seasons below this are too noisy to fit a spread on
SHRINK_GAMES = 10.0        # games of evidence needed to move halfway off the positional curve
RESID_MIN = 40             # keep a residual pool only if it has at least this many weeks
FLOOR = -3.0               # a weekly score below this has essentially never happened

# Used only when history has nothing to say about a position.
FALLBACK_SD = {"QB": 7.5, "RB": 7.5, "WR": 8.0, "TE": 6.0, "K": 4.0, "DST": 6.0}
CACHE_FILE = CACHE / "variance.json"


# ------------------------------------------------------------------- fitting
def _weekly_scores(seasons):
    """Every player-week in `seasons` scored under this league's rules, as
    (key, position, season, week, points)."""
    from .history import load_weekly
    w = load_weekly(seasons)
    frames = []

    o = w[w["position"].isin(OFF_POS)].copy()
    o["pts"] = weekly_points_from_history(o, S["scoring_detail"])
    frames.append(o[["player_display_name", "position", "season", "week", "pts"]])

    k = w[w["position"] == "K"].copy()
    if not k.empty:
        k["pts"] = kicker_points_from_history(k, S.get("kicking_detail", {}))
        frames.append(k[["player_display_name", "position", "season", "week", "pts"]])

    df = pd.concat(frames, ignore_index=True)
    df["key"] = df["player_display_name"].map(norm_name)
    return df[["key", "position", "season", "week", "pts"]]


def _dst_weekly(seasons):
    """Team defences scored under league rules, week by week. Separate from players because
    the inputs are team stats and the opponent's output, not a box score line."""
    try:
        import nflreadpy as nfl
        from .names import norm_team
        t = nfl.load_team_stats(list(seasons)).to_pandas()
        t = t[t["season_type"] == "REG"].copy()
        t["team"] = t["team"].map(norm_team); t["opponent_team"] = t["opponent_team"].map(norm_team)
        sch = nfl.load_schedules(list(seasons)).to_pandas()
        sch["home_team"] = sch["home_team"].map(norm_team); sch["away_team"] = sch["away_team"].map(norm_team)
        scored = {}
        for _, r in sch.iterrows():
            if pd.isna(r.get("result")):
                continue
            scored[(r["season"], r["week"], r["home_team"])] = r["home_score"]
            scored[(r["season"], r["week"], r["away_team"])] = r["away_score"]
        off = t.set_index(["season", "week", "team"])
        yds, pa = [], []
        for _, r in t.iterrows():
            idx = (r["season"], r["week"], r["opponent_team"])
            if idx in off.index:
                row = off.loc[idx]
                yds.append(float(row["passing_yards"]) + float(row["rushing_yards"]))
            else:
                yds.append(0.0)
            pa.append(scored.get(idx, 0))
        t["yards_allowed"] = yds
        t["points_allowed"] = pa
        t["pts"] = dst_points_from_history(t, S.get("dst_detail", {}))
        t["key"] = t["team"]
        t["position"] = "DST"
        return t[["key", "position", "season", "week", "pts"]]
    except Exception:
        return pd.DataFrame(columns=["key", "position", "season", "week", "pts"])


def _fit_curve(ps):
    """Least-squares sd = a + b * ppg for one position's player-seasons."""
    x = ps["ppg"].to_numpy(float)
    y = ps["sd"].to_numpy(float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 8:
        return None
    b, a = np.polyfit(x, y, 1)
    return float(a), float(b)


def fit(seasons=None, hold_out=None):
    """Fit the whole model. `hold_out` excludes a season so it can be used for calibration."""
    seasons = list(seasons or S["history_seasons"])
    df = pd.concat([_weekly_scores(seasons), _dst_weekly(seasons)], ignore_index=True)
    if hold_out is not None:
        df = df[df["season"] != hold_out]

    ps = (df.groupby(["key", "position", "season"])
            .agg(games=("week", "nunique"), ppg=("pts", "mean"), sd=("pts", "std"))
            .reset_index())
    fit_pool = ps[(ps["games"] >= MIN_GAMES_FIT) & ps["sd"].notna()]

    curve, resid, player = {}, {}, {}
    for pos, grp in fit_pool.groupby("position"):
        c = _fit_curve(grp)
        if c:
            curve[pos] = c

    # per-player scale: how much wider or narrower than the curve he actually was,
    # shrunk toward 1 by the number of games behind it
    agg = (fit_pool.groupby(["key", "position"])
                   .apply(lambda g: pd.Series({"games": g["games"].sum(),
                                               "ppg": np.average(g["ppg"], weights=g["games"]),
                                               "sd": np.average(g["sd"], weights=g["games"])}),
                          include_groups=False)
                   .reset_index())
    for _, r in agg.iterrows():
        pos = r["position"]
        if pos not in curve:
            continue
        pred = max(1.0, curve[pos][0] + curve[pos][1] * r["ppg"])
        raw = r["sd"] / pred
        n = float(r["games"])
        shrunk = (n * raw + SHRINK_GAMES * 1.0) / (n + SHRINK_GAMES)
        player[r["key"]] = round(float(np.clip(shrunk, 0.5, 2.0)), 3)

    # standardised residual pools, split by position and by whether the player was a
    # low or high scorer, because a WR3's week is shaped differently from a WR1's
    means = ps.set_index(["key", "position", "season"])["ppg"].to_dict()
    df = df.copy()
    df["ppg"] = [means.get((k, p, s), np.nan) for k, p, s in zip(df["key"], df["position"], df["season"])]
    med = fit_pool.groupby("position")["ppg"].median().to_dict()
    for pos, grp in df.groupby("position"):
        if pos not in curve:
            continue
        for band in ("lo", "hi"):
            cut = med.get(pos, 0)
            g = grp[grp["ppg"] < cut] if band == "lo" else grp[grp["ppg"] >= cut]
            g = g[g["ppg"].notna()]
            if len(g) < RESID_MIN:
                continue
            pred = np.maximum(1.0, curve[pos][0] + curve[pos][1] * g["ppg"].to_numpy(float))
            z = (g["pts"].to_numpy(float) - g["ppg"].to_numpy(float)) / pred
            z = z[np.isfinite(z)]
            if len(z) < RESID_MIN:
                continue
            z = (z - z.mean()) / (z.std() or 1.0)     # mean 0, sd 1 by construction
            resid[f"{pos}_{band}"] = [round(float(v), 4) for v in z]
    return {"curve": curve, "resid": resid, "player": player,
            "median_ppg": {k: float(v) for k, v in med.items()},
            "seasons": seasons, "hold_out": hold_out}


def load(force=False):
    """Fitted model, from cache when available."""
    if not force and CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    m = fit()
    CACHE.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(m))
    return m


# ------------------------------------------------------------------- using it
def sd_for(model, pos, mu, key=None):
    """Standard deviation of this player's week, given what he is projected to score."""
    c = model["curve"].get(pos)
    if not c:
        return FALLBACK_SD.get(pos, 7.0)
    base = max(1.0, c[0] + c[1] * max(0.0, mu))
    return base * (model["player"].get(key, 1.0) if key else 1.0)


def _pool(model, pos, mu):
    band = "hi" if mu >= model.get("median_ppg", {}).get(pos, 0) else "lo"
    return (model["resid"].get(f"{pos}_{band}")
            or model["resid"].get(f"{pos}_{'lo' if band == 'hi' else 'hi'}"))


def sample_player(model, pos, mu, key, n, rng, frac=1.0):
    """`n` simulated scores for one player.

    `frac` is how much of his game is still to be played, so a player at halftime
    contributes half the mean and, because variance accumulates with time, 1/sqrt(2) of
    the spread. A finished player contributes nothing further.
    """
    if frac <= 0 or mu <= 0:
        return np.zeros(n)
    sd = sd_for(model, pos, mu, key) * math.sqrt(frac)
    pool = _pool(model, pos, mu)
    z = rng.choice(pool, size=n) if pool else rng.standard_normal(n)
    return np.maximum(FLOOR * frac, mu * frac + sd * z)


def sample_team(model, starters, n, rng):
    """`n` simulated finals for one lineup.

    Each starter is a dict with pos, proj, key, an optional `banked` for points already
    scored, and `frac` for how much of his game is left.
    """
    total = np.zeros(n)
    for p in starters:
        total += p.get("banked", 0.0)
        total += sample_player(model, p["pos"], p.get("proj", 0.0), p.get("key"),
                               n, rng, p.get("frac", 1.0))
    return total


def player_range(model, pos, mu, key, banked=0.0, frac=1.0, n=4000, seed=11):
    """One player's floor, likely score and ceiling: 10th, 50th and 90th percentile of where
    he finishes, counting what he has already banked."""
    if frac <= 0 or mu <= 0:
        return {"floor": round(banked, 1), "mid": round(banked, 1), "ceiling": round(banked, 1)}
    s = banked + sample_player(model, pos, mu, key, n, np.random.default_rng(seed), frac)
    return {"floor": round(float(np.percentile(s, 10)), 1),
            "mid": round(float(np.median(s)), 1),
            "ceiling": round(float(np.percentile(s, 90)), 1)}


def matchup(model, a_starters, b_starters, n=20000, seed=7):
    """P(a beats b), plus each side's simulated median and 10th-90th percentile range."""
    rng = np.random.default_rng(seed)
    a = sample_team(model, a_starters, n, rng)
    b = sample_team(model, b_starters, n, rng)
    return {"p": float((a > b).mean() + 0.5 * (a == b).mean()),
            "a_median": float(np.median(a)), "b_median": float(np.median(b)),
            "a_lo": float(np.percentile(a, 10)), "a_hi": float(np.percentile(a, 90)),
            "b_lo": float(np.percentile(b, 10)), "b_hi": float(np.percentile(b, 90))}
