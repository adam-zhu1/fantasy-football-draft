# Backtest: does the decision layer help?

`python backtest.py --year 2025 --n 300` and `--year 2024`. Each draft: 12-team snake, my slot random,
opponents draft from an ADP-centred distribution with roster-need logic. Rosters scored on the real season
(weeks 1–14, weekly best lineup, no in-season moves). Run Sep 2, 2026.

Strategies (all start from the SAME preseason market ranking, FantasyFootballCalculator PPR ADP):
- `adp_naive`  – take the best remaining player by ADP (max 2 QB / 2 TE). A typical casual drafter.
- `adp_rules`  – ADP order but with the plan's hard roster rules (1 QB, 1 TE, 5 RB / 6 WR minimums).
- `raw_vbd`    – value over replacement on the ADP-implied projection, roster rules + need bonus.
- `floor_vbd`  – the engine: games-played prior + weekly-variance penalty, then VBD, rules, need bonus.

| 2025 | mean pts | 10th pct | mean rank | P(last) | P(bottom 3) | P(top 4) |
|---|---|---|---|---|---|---|
| adp_naive | 1516 | 1346 | 5.33 | 5.3% | 14.7% | 45.7% |
| adp_rules | 1456 | 1256 | 6.62 | 10.0% | 26.0% | 34.3% |
| raw_vbd | 1466 | 1284 | 6.46 | 6.7% | 26.0% | 34.0% |
| **floor_vbd** | 1496 | 1331 | 5.62 | **4.0%** | **14.0%** | 43.7% |

| 2024 | mean pts | 10th pct | mean rank | P(last) | P(bottom 3) | P(top 4) |
|---|---|---|---|---|---|---|
| adp_naive | 1536 | 1359 | 6.13 | 8.3% | 25.7% | 41.7% |
| adp_rules | 1512 | 1350 | 6.63 | 7.7% | 25.7% | 32.0% |
| raw_vbd | 1547 | 1429 | 5.67 | 1.0% | 12.0% | 39.0% |
| **floor_vbd** | 1597 | 1476 | 4.35 | **1.0%** | **4.7%** | 57.3% |

Chance level for P(last) is 8.3% (1 in 12).

## Read this honestly
- The floor model had the lowest last-place rate in both seasons, and the highest 10th-percentile
  score. In 2024 the gap is large; in 2025 the gap over the naive drafter is inside the noise of 300 drafts.
- The roster rules alone (`adp_rules`) made things worse. They only pay off combined with the value model.
- Scoring uses the hindsight-best lineup each week, which flatters rosters carrying two QBs or TEs
  (the naive strategy), so the naive numbers are a bit optimistic.
- Historical consensus projections aren't freely available, so projections are proxied by ADP rank mapped
  onto a positional points curve. The real draft uses actual FantasyPros projections, which should help.
- Committee and TD-dependence penalties and the opponent simulation were not part of the backtest.
- No in-season management (waivers, IR, lineup mistakes). That is where the rest of the anti-last-place work lives.
