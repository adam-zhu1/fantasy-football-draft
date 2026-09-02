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
