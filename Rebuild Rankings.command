#!/bin/zsh
# Run this after re-exporting fresh FantasyPros CSVs into data/. Rebuilds board.csv + board.txt.
cd "$(dirname "$0")"
source .venv/bin/activate
python build_board.py --top 150 | tee board.txt | head -60
echo; echo "Done. board.csv and board.txt updated. Restart the draft board to load them."
read -k1 "?Press any key to close."
