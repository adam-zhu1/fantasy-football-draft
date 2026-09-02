#!/bin/zsh
# Double-click this file on draft day. It starts the board and opens it in your browser.
cd "$(dirname "$0")"
source .venv/bin/activate
(sleep 2 && open http://127.0.0.1:5055) &
python draft_server.py
