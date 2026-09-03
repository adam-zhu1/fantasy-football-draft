#!/bin/zsh
# Double-click on draft day. Starts the board (or just opens it if already running) and restarts it if it ever crashes.
cd "$(dirname "$0")"
if curl -s -o /dev/null http://127.0.0.1:5055/api/state; then
  echo "Draft board already running. Opening it."; open http://127.0.0.1:5055; sleep 2; exit 0
fi
source .venv/bin/activate || { echo "venv missing: run  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; read -k1; exit 1; }
[ -f board.csv ] || { echo "board.csv missing: run Rebuild Rankings.command first"; read -k1; exit 1; }
(sleep 2 && open http://127.0.0.1:5055) &
for attempt in 1 2 3 4 5; do
  python draft_server.py
  echo "Server stopped (attempt $attempt). Your picks are saved in data/draft_state.json. Restarting in 2s..."
  sleep 2
done
read -k1 "?Server stopped 5 times. Press any key to close, then tell Claude."
