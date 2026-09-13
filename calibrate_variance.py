#!/usr/bin/env python
"""Check the variance model against a season it never saw.

Fits on the earlier seasons, holds out the last one, then asks of every held-out
player-week: where did the actual score fall inside the distribution we predicted? Those
positions should be spread evenly over 0 to 1. If they bunch in the middle the model is
calling games more uncertain than they are; if they pile up at the ends it is too
confident. The old fixed-standard-deviation normal is scored the same way for comparison.

    python calibrate_variance.py
    python calibrate_variance.py --hold-out 2024
"""
import argparse
import math

import numpy as np
import pandas as pd

from ffdraft import variance as V

ap = argparse.ArgumentParser()
ap.add_argument("--hold-out", type=int, default=2025, help="season to test on")
ap.add_argument("--min-games", type=int, default=6, help="drop tiny samples from the test set")
a = ap.parse_args()

seasons = list(V.S["history_seasons"])
model = V.fit(seasons, hold_out=a.hold_out)

df = pd.concat([V._weekly_scores(seasons), V._dst_weekly(seasons)], ignore_index=True)
test = df[df["season"] == a.hold_out].copy()
if test.empty:
    raise SystemExit(f"no {a.hold_out} weeks in history_seasons {seasons}")

# Stand-in for a projection: what the player averaged that season. The mean is therefore
# in-sample, but the spread and shape being tested were fitted without this season.
ps = test.groupby(["key", "position"]).agg(games=("week", "nunique"), ppg=("pts", "mean")).reset_index()
test = test.merge(ps[ps["games"] >= a.min_games][["key", "position", "ppg"]], on=["key", "position"])

new, old = [], []
for pos, g in test.groupby("position"):
    if pos not in model["curve"]:
        continue
    mu = g["ppg"].to_numpy(float)
    act = g["pts"].to_numpy(float)
    scale = np.array([model["player"].get(k, 1.0) for k in g["key"]])
    sd = np.maximum(1.0, model["curve"][pos][0] + model["curve"][pos][1] * mu) * scale
    med = model["median_ppg"].get(pos, 0)
    for i in range(len(g)):
        pool = model["resid"].get(f"{pos}_{'hi' if mu[i] >= med else 'lo'}") or model["resid"].get(f"{pos}_hi")
        new.append(float(np.mean(np.asarray(pool) <= (act[i] - mu[i]) / sd[i])))
    sd_old = V.FALLBACK_SD.get(pos, 7.0)
    old.extend(0.5 * (1 + math.erf(z / math.sqrt(2))) for z in (act - mu) / sd_old)


def report(name, pits):
    p = np.asarray(pits)
    share = np.histogram(p, bins=10, range=(0, 1))[0] / len(p)
    print(f"{name:>6}  weeks {len(p):6d}   uniformity gap {np.abs(share - 0.10).sum() / 2:5.3f}   "
          f"80% band holds {((p >= .10) & (p <= .90)).mean():5.1%}   "
          f"50% band holds {((p >= .25) & (p <= .75)).mean():5.1%}")
    print("         deciles " + " ".join(f"{s:4.2f}" for s in share))


print(f"Held out {a.hold_out}, fitted on {[s for s in seasons if s != a.hold_out]}.")
print("Perfect would be: uniformity gap 0.000, 80% band holds 80.0%, 50% holds 50.0%\n")
report("new", new)
report("old", old)
