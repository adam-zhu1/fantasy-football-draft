#!/usr/bin/env python
"""Weekly report to the terminal and weekN_report.md. See ffdraft/season.py. Usage: python week.py [--week N]"""
import argparse
from ffdraft.config import ROOT
from ffdraft.season import compute, render_markdown

ap = argparse.ArgumentParser(); ap.add_argument("--week", type=int); a = ap.parse_args()
d = compute(a.week)
md = render_markdown(d)
(ROOT / f"week{d['week']}_report.md").write_text(md)
print(md)
