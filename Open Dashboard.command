#!/bin/zsh
# Opens dashboard.html, rebuilding first if it is stale. The LaunchAgent normally keeps it
# fresh on its own; this is for when you want the newest numbers right now.
cd "$(dirname "$0")"
source .venv/bin/activate
python build_dashboard.py --force
open dashboard.html
