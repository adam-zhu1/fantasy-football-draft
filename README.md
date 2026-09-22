# Fantasy draft assistant — RIP EVAN LU (12-team, full PPR, ESPN)

## Run the board
    source .venv/bin/activate
    python build_board.py            # prints tiered board, writes board.csv and (via > board.txt) a printable copy
    python build_board.py --risk 0.3 # try a different risk aversion
    python build_board.py --top 150 > board.txt

## Files
- `settings.json` — league settings (teams, lineup, scoring). Fill `my_draft_slot` when the order is posted.
- `data/FantasyPros_*.csv` — projections + ADP exports. Re-export the morning of the draft and rerun.
- `data/flags.csv` — manual committee overrides: `player,committee,note` with committee 1 or 0.
- `data/cache/` — nflverse weekly stats 2023–2025 (auto-downloaded once).
- `board.csv` / `board.txt` — the output. Print board.txt as the paper fallback.
- `ffdraft/` — code: projections.py (parse exports), scoring.py (ESPN scoring), history.py (games-played + weekly-SD priors), vbd.py (floor value, baselines, VBD, tiers).

## How a player's value is computed
1. Season points recomputed from projected stats under THIS league's scoring (not FantasyPros' preset).
2. ppg = points / 17. Expected games = blend of the player's own 2023–25 games-played rate and the positional average (~15). Rookies get the positional average.
3. Weekly SD taken from history by (position, tier of 12).
4. floor = expected_games x (ppg − risk_aversion x weekly_SD), then −10% if committee back, −5% if TD-dependent (RB/WR/TE only).
5. VBD = floor − floor of the replacement player (QB12, RB30, WR36, TE12, K12, DST12; derived from settings).
6. Tiers: new tier when the VBD gap to the next player is large or the tier gets too wide.

## Rules for draft day (from the spec)
- Never reach across a tier break.
- K and DST only in the last two rounds.
- One QB, one TE. Everything else RB/WR until you have 5 RB and 6 WR.

## Draft day (Part 3)
1. Double-click **Start Draft Board.command** (or `python draft_server.py`). It opens http://127.0.0.1:5055.
2. Set your slot (top right) when ESPN posts the order.
3. Each time anyone picks: type the name, Enter = someone else, Shift+Enter = me. Undo with Cmd+Z.
4. When it says YOUR PICK, take the recommendation (or anyone in the same tier), then mark him as yours.
State is saved to `data/draft_state.json` after every pick; restarting loses nothing. "Reset draft" clears it.

Morning of the draft: re-export the FantasyPros files into `data/`, then double-click **Rebuild Rankings.command**.

## Opponent modeling
Every pick is attributed to a team from the snake order, so the tool knows each opponent's roster.
Before each of your turns it simulates the picks in between (400 runs): bot teams take the best
remaining player by ESPN rank; human teams draw from an ADP-centred distribution weighted by what
their roster still needs. That gives each player's chance of surviving to your pick, and the
expected best player left at each position (cost of waiting), which feeds the recommendation.
Tick "bot" on any slot with no manager. Fix a mis-attributed pick with the team dropdown in the log.

## Saturday checklist
1. Morning: re-export the 5 FantasyPros files into `data/`, double-click **Rebuild Rankings.command**, print `board.txt`.
2. Double-click **Start Draft Board.command**. It reopens if already running and auto-restarts if it crashes; picks persist in `data/draft_state.json`.
3. One hour before: paste the 12 team names in draft order into Claude (or type them on the team cards), set your slot, tick bots on managerless teams.
4. Draft: type each pick as it happens. Enter = someone else, Shift+Enter = me. Cmd+Z undoes. Take the recommendation or anyone in its tier.
5. If the page dies and won't come back: draft off the printed `board.txt`, crossing names off.

## In-season (Part 5)

### The dashboard (just reload the file)

    open dashboard.html        # or double-click "Open Dashboard.command"

`dashboard.html` is the season dashboard with its data baked in. It needs no server, no
network and no terminal: bookmark `file:///Users/adamzhu/Projects/fantasy-football-draft/dashboard.html`
and reload it. A LaunchAgent (`~/Library/LaunchAgents/com.adamzhu.fantasy-dashboard.plist`)
runs `build_dashboard.py` every 10 minutes and at login; the script itself decides whether a
rebuild is due -- every 10 minutes while games are on (Thu/Sun/Mon evenings), hourly
otherwise -- so most firings cost nothing. The header shows when the file was built, so a
stale one is visible. Remove the agent with
`launchctl bootout gui/$(id -u)/com.adamzhu.fantasy-dashboard`.

The page also reloads itself every 5 minutes, because rebuilding the file does not reach a
tab that is already open: leaving it up on a second monitor during games is enough, with no
keypress. It carries the current tab, week and scroll position across the reload in
`sessionStorage`, so it lands where it was instead of snapping back to the top.

This replaced a long-running Flask server as the default way in. A server is a process, and
a process can die or go stale silently: one started on Sep 9 2026 was still serving
two-week-old code on Sep 22 with nothing on the page to say so. A built file cannot rot that
way, because every build runs current code from scratch.

`season_server.py` is still there for live recomputation on demand (`Start Season
Dashboard.command`, http://127.0.0.1:5056). It is the only way to use the Manage tab, since
the built file has nothing to POST to. Both render from the same `templates/season.html`.

### The weekly report

    python week.py             # this week's report -> weekN_report.md
Prints the lineup to enter in ESPN (with lock times), alerts (byes, missing from rankings), your matchup win
probability, predictions for every matchup, power rankings, and waiver targets. Uses FantasyPros weekly
consensus via nflverse (refreshes daily), so re-run Saturday night or Sunday morning for the latest.

Once games start it scores the week live from ESPN's public box scores (`ffdraft/live.py`), which update
within about a minute. A finished player counts his actual points; a player mid-game counts what he has
already scored plus the share of his projection matching the share of the game still to play, and carries
only that share of the weekly variance. Scoring comes from `scoring_detail` / `kicking_detail` /
`dst_detail` in settings.json, transcribed from ESPN's League > Settings > Scoring page — re-check them if
the commissioner changes anything. Applying those rules to ESPN's box score reproduces ESPN's own fantasy
points exactly, so the two should never disagree.

Do not go back to the nflverse feed for live scoring. It publishes finals hours late: on Sun Sep 13 2026 at
4:15pm ET it still had BAL@IND unplayed after a 41-23 final, which showed the Week 1 opponent on 35.3 when
he was really on 104.26 and put the matchup at 75% the wrong way. nflverse is kept only as the fallback for
when ESPN is unreachable, and the report says so in a callout when it falls back.

### The league API (real lineups)

With `data/espn_auth.json` in place, `ffdraft/espn_api.py` reads the league from ESPN's fantasy feed as
you: every team's real starting lineup, ESPN's own weekly finals, and all 12 rosters. The file holds your
`espn_s2` and `SWID` browser cookies plus the league id, is gitignored and chmod 600, and is a login
credential rather than a config value. Without it every function there returns None and the tool falls
back to the guess described below, so it still runs for anyone who has not set it up.

This closed the largest error in the model. Before it, we knew every team's roster but not which players
the other managers actually started, so opponent totals assumed each manager starts his best-projected
lineup. Measured against ESPN's Week 2 finals that ran a mean 10.6 points off across the twelve teams, in
both directions -- Walter was 32 points underrated, Prithish 27 points over -- and only the two lineups
read live from the box score (yours and your opponent's) were right. Those totals feed team ratings, which
feed the playoff number the dashboard tells you to steer by.

Rosters re-sync from the feed on every run, so the waiver wire no longer silently rots the file.
`prior_results` prefers ESPN's own final for a completed week over re-scoring the box score: the official
number already carries stat corrections, and it still counts a player who has since been dropped and whom
the current roster no longer lists. After backfilling Weeks 1-2 this way, every team's rebuilt record and
points-for match ESPN's standings page exactly.

If the cookies expire (logging out of ESPN everywhere invalidates them), the feed starts returning None and
the tool quietly reverts to guessing. Re-copy both values from Chrome DevTools > Application > Cookies.

Lineups freeze once a week kicks off, into `data/week_lineups.json` (local-only). Without that, re-running
after the next scrape re-picks a finished week's starters using projections that did not exist at kickoff:
the Monday Week 1 scrape swapped an 18-point tight end into the opponent's lineup in place of the 2.6 he
actually started, inflating him by 15. Every week's entry is now overwritten from the league API with the
lineup each manager really submitted, which is why the guessed snapshots for Weeks 1-2 were discarded
rather than kept. Without the cookies, entries fall back to the best-projected lineup frozen after the
fact; to correct one by hand, edit that file. Deleting it re-freezes every week from today's projections,
which is wrong for weeks already played.

### Injuries

Designations come from ESPN's public injuries feed automatically (`live.injuries()`), so the report no
longer tells you to go and check the Q / D / O tags yourself. A player ESPN lists as Out, on Injured
Reserve or suspended has his projection zeroed, which stops a stale number from starting him or inflating
an opponent. Doubtful and Questionable are flagged loudly but left alone, since they are warnings rather
than certainties. Your opponent's designations are listed too, because they move the matchup.

### How uncertain a week is (`ffdraft/variance.py`)

Win probabilities come from simulating both lineups 20,000 times, not from a bell curve. Each player's
spread is fitted from 2023-25 history three ways: a straight line in scoring level per position
(`sd = a + b * projection`, so a 20-point receiver swings harder than a 5-point one), a per-player scale for
whoever is genuinely streakier than the curve (shrunk toward the curve by games played, so small samples do
not run wild), and a pool of real standardised residuals to draw from instead of a normal, which keeps
fantasy scoring's right skew. Fitted once into `data/cache/variance.json`; delete that file to refit.

    python calibrate_variance.py                 # hold out the last season and check the fit
    python calibrate_variance.py --hold-out 2024

Holding out a season the model never saw, the actual scores land evenly across the predicted distribution
(uniformity gap 0.02, the 80% band holding 79.6% and the 50% band 50.9%). The old fixed standard deviations
were far too wide: their 80% band held 88.6% and their 50% band 66.5%.

Be honest about where this pays off. Summing nine starters averages the per-player differences away, so
team-level win probability barely moves: across Week 1's six matchups the old and new numbers differ by 0.3
points of probability on average. The gain is per player, and it shows up in the two places a single
player's spread is the whole question:

- The **Floor** and **Ceiling** columns in the lineup table, the 10th and 90th percentile of where each
  starter lands. The old model could not tell a steady possession receiver from a boom-or-bust deep threat.
- The **lean**. Below 35% the report tells you to maximise ceiling rather than projection, because losing
  by less is worth nothing; above 65% it tells you to protect the floor. It then names any bench swap that
  actually improves the thing you should be maximising, which is often none.

Rosters live in data/league_rosters.json and matchups in data/league_schedule.json (both local-only).

### Team strength that learns (`ffdraft/ratings.py`)

The outlook used to rate every team by its draft-day projection for the whole season, so
results never changed anyone's rating and the playoff and last-place numbers stayed vague.
Ratings now blend the draft projection with actual scoring, weighted by evidence:

    w = (n / sigma^2) / (1 / tau^2 + n / sigma^2)

`sigma` is a team's week-to-week spread and `tau` is how wrong a draft projection can be
about a team's true level. Both are estimated from the league's own scoring from week 3
onward; before that they fall back to 21 and 6. The league's overall scoring level is
corrected separately and much faster, because it averages over every team and week: in 2026
the draft projections sat about 15 points a week below what teams actually scored, a bias
shared by all twelve and therefore invisible in head-to-head play.

Treat weeks 1 and 2 as provisional. Early output is sensitive to `tau`: after Week 1 2026 the
bottom team's last-place probability ran from 29% at tau=3 to 87% at tau=25. From week 3 the
data sets it and the sensitivity goes away.

Two checks worth re-running after changes: expected wins across the twelve teams must sum to
84 (12 teams x 14 weeks / 2), and last-place probabilities must sum to 1.

### Season dashboard
Double-click **Start Season Dashboard.command** (or `python season_server.py`) → http://127.0.0.1:5056.
Tabs: Lineup (what to set in ESPN, lock times, alerts, close calls), My matchup (win probability), Predictions,
Power rankings, Waivers, Rosters, Manage (record adds/drops; paste the ESPN schedule page to load matchups).
