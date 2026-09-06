#!/bin/zsh
cd "$(dirname "$0")"
if curl -s -o /dev/null http://127.0.0.1:5056/api/week; then echo "Already running."; open http://127.0.0.1:5056; sleep 2; exit 0; fi
source .venv/bin/activate
(sleep 3 && open http://127.0.0.1:5056) &
for attempt in 1 2 3 4 5; do python season_server.py; echo "Server stopped (attempt $attempt). Restarting in 2s..."; sleep 2; done
