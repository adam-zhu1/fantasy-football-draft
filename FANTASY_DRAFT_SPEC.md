# Fantasy Football Draft Assistant — Project Spec

## Objective

**Minimize probability of finishing last**, not maximize probability of winning. This is a
floor-maximization problem, not a ceiling-maximization problem. Every design decision below
follows from that.

Do not build a points projection model. Consume free consensus projections. The edge is in
the decision layer.

---

## Part 1: Data layer

### Required inputs

1. **Consensus projections** — FantasyPros allows CSV export of consensus projections and
   average draft position (ADP) with a free account. Download for the exact scoring format
   of the league (standard / half-PPR / full-PPR — these produce very different boards).
   Get: player name, team, position, projected season points, projected games, ADP.

2. **Injury/availability prior** — needed to convert season totals to floor-adjusted value.
   Cheap version: use each player's projected games from the source. Better version: pull
   the last 3 seasons of games-played per player from `nfl_data_py` and blend.

3. **Weekly variance priors** — from `nfl_data_py` weekly stats for 2023–2025, compute the
   standard deviation of weekly fantasy points by (position, positional finish tier). Example
   output: "WRs finishing 13–24 have a weekly SD of X." Use these as variance estimates for
   this year's players by their projected tier. Do not try to estimate per-player variance
   from small samples.

4. **Live draft state** — see Part 4.

### Libraries

```
pandas, numpy, nfl_data_py, requests
```

`nfl_data_py` pulls from nflverse GitHub releases: play-by-play back to 1999, weekly player
stats, snap counts, rosters, schedules. Free, no auth.

Sleeper's API (`api.sleeper.app`) is open with no authentication and has player metadata and,
if the league is hosted there, live draft picks. Verify current endpoint paths against their
docs — endpoint details may have changed.

---

## Part 2: Value-based drafting engine (core)

Raw projected points are useless for cross-position comparison. A QB projected for 320 points
is not better than a RB projected for 240, because the QB you could get for free is also
projected for 250 while the free RB is projected for 90.

### Replacement level

Compute the baseline as the projected points of the last player at each position who gets
started league-wide. For a 12-team league starting 1QB / 2RB / 2WR / 1TE / 1FLEX:

| Position | Baseline |
|---|---|
| QB | QB12 |
| RB | RB30 |
| WR | WR36 |
| TE | TE12 |
| K | K12 |
| DEF | DEF12 |

RB and WR baselines are pushed past the raw starter count (24 each) because the flex slot
absorbs extra RB/WR. Adjust these numbers for actual league size and starting requirements —
derive them from the settings rather than hardcoding.

**Superflex leagues are the big exception.** If the league lets you start a second QB, the QB
baseline moves to roughly QB24 and QB value roughly doubles. Confirm this setting before
drafting.

### VBD

```
VBD(player) = projected_points(player) - baseline_points(player.position)
```

Rank all players by VBD across positions. This is your raw board.

### Floor adjustment (this is the part specific to your objective)

```
floor_value(player) =
      (projected_points_per_game * expected_games_played)
    - (RISK_AVERSION * weekly_sd)
```

Set `RISK_AVERSION` around 0.3–0.5 and expose it as a tunable. Then compute VBD on
`floor_value` instead of raw projected points.

Additional multiplicative penalties:

- **Committee/unclear role**: -10% if the player shares a backfield or has an unsettled
  depth chart position. Requires a manual flag column; that's fine, you'll fill in ~30 rows.
- **TD dependence**: if projected TDs contribute more than ~35% of a player's projected
  points, apply -5%. High-TD-share players are the volatile ones.

---

## Part 3: Live draft board

This is what you actually use on Saturday. It must be fast to operate under a 60–90 second
clock.

### Requirements

- Terminal or simple local web UI. Show top ~15 available players by adjusted VBD.
- One-keystroke way to mark a player as drafted (by anyone). Fuzzy name matching is
  mandatory — you will not have time to type full names correctly.
- Track your own roster and remaining slots.
- **Tiers**: cluster players into tiers by VBD gaps (use a simple gap threshold, or
  1-D k-means). Within a tier, players are near-equivalent, so break ties by roster need.
  This is the single most useful display feature. Never reach across a tier break.

### Pick recommendation score

```
score(player) =
      adjusted_VBD(player)
    + positional_need_bonus(player.position, my_roster)
    - same_team_penalty(player, my_roster)      # -8% if I already own a starter
                                               #  from this NFL offense
    - bye_stack_penalty(player, my_roster)      # scale up sharply past 3 starters
                                               #  sharing a bye week
```

### Hard constraints to encode

- Never draft K or DEF before the final two rounds. No exceptions.
- Exactly one QB (unless superflex), one TE, one K, one DEF. Every other pick is RB or WR.
- By the end of the draft: minimum 5 RB and 6 WR on the roster. Depth at the two positions
  that suffer injuries is the primary defense against finishing last.

---

## Part 4: Stretch goal (only if Parts 1–3 are done by Thursday night)

**Monte Carlo draft simulator.** Simulate the other 11 managers picking from a distribution
centered on ADP with realistic noise (roughly normal, SD of 6–10 picks, plus positional-need
logic). Use it for:

1. **Value over next available (VONA)** — the real question at your pick isn't "who is best"
   but "who will still be there next turn." Compare each candidate's value to the expected
   best available at that position when your next pick comes around. Take the player whose
   value would decay most.
2. **Pre-testing strategies** — run 1000 drafts from your seat under different rules
   (RB-early vs WR-early, when to take QB) and compare distributions of final roster floor
   value. Look specifically at the 10th percentile outcome, since that's the last-place
   scenario you're trying to eliminate.

---

## Part 5: In-season (build after Saturday)

Do not attempt before the draft. But this is where most of the anti-last-place value
actually lives:

1. **Bye week and inactive alarm.** A script that checks your starting lineup against bye
   weeks and injury designations and shouts at you. Starting a player on bye costs you a
   zero at that slot, which usually costs the whole matchup. This is the highest-value
   thing in the entire project relative to lines of code.
2. **Win-probability lineup optimizer.** Simulate your score distribution and your
   opponent's, then choose the lineup that maximizes P(win), not expected points. When
   you're a favorite, start the consistent player. When you're an underdog, start the
   volatile one. Most managers get both cases wrong.
3. **Waiver evaluator.** Rank available players by expected floor value added over your
   current worst starter for the rest of the season. Ignore last week's box score.

---

## Suggested schedule

| When | Task |
|---|---|
| Wed night | Projections + ADP loaded into a DataFrame. VBD engine with correct baselines. Print a ranked board. |
| Thu | Floor adjustment, variance priors from history, tiering, live draft tracker with fuzzy matching. |
| Fri | Run 2–3 free mock drafts (Sleeper and FantasyPros both offer instant mocks against bots). Operate the tool live. Fix what's slow. |
| Sat | Draft. Trust the tiers. |

The Friday mock drafts are not optional. An untested tool under a live draft clock is worse
than a printed sheet of paper.

---

## Fallback

If the code isn't ready by Saturday, print the tiered VBD board as a PDF and draft off it
by hand. A correct board on paper beats a broken tool. Build the board first for this reason.
