import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"

DEFAULTS = {
    "risk_aversion": 0.4,
    "games_in_season": 17,
    "flex_share": {"RB": 0.5, "WR": 1.0},   # how the FLEX slots split between RB and WR (spec: RB30/WR36 in 12-team)
    "committee_penalty": 0.10,
    "td_dependence_threshold": 0.35,
    "td_dependence_penalty": 0.05,
    "history_seasons": [2023, 2024, 2025],
    "prior_shrink_seasons": 2.0,
    "market_weight": 0.6,                 # blend: (1-w)*model VBD + w*market-implied VBD          # weight of positional prior vs player history, in "seasons"
}


def load_settings(path=None):
    path = Path(path) if path else ROOT / "settings.json"
    with open(path) as f:
        s = json.load(f)
    for k, v in DEFAULTS.items():
        s.setdefault(k, v)
    return s
