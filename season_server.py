#!/usr/bin/env python
"""Season dashboard. Run `python season_server.py` and open http://127.0.0.1:5056"""
import json, traceback
from flask import Flask, jsonify, render_template, request

from ffdraft.config import ROOT
from ffdraft.season import compute, load_rosters, save_rosters, load_matchups, save_matchups, parse_schedule_paste, short, _CACHE

app = Flask(__name__, template_folder=str(ROOT / "templates"))


@app.errorhandler(Exception)
def _err(e):
    traceback.print_exc()
    return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.get("/")
def index():
    return render_template("season.html")


@app.get("/api/week")
def api_week():
    w = request.args.get("week", type=int)
    force = request.args.get("refresh") == "1"
    return jsonify(compute(w, force))


@app.post("/api/transaction")
def api_transaction():
    """{"team": "green fn", "add": "Name", "add_pos": "WR", "drop": "Name"}"""
    b = request.get_json(force=True)
    L = load_rosters()
    full = next((k for k in L["rosters"] if short(k) == b["team"]), None)
    if not full:
        return jsonify({"error": "unknown team"}), 400
    r = L["rosters"][full]
    if b.get("drop"):
        for pos, ps in r.items():
            if b["drop"] in ps: ps.remove(b["drop"])
    if b.get("add"):
        r.setdefault(b.get("add_pos", "WR"), []).append(b["add"].strip())
    save_rosters(L)
    _CACHE.pop("board", None)
    return jsonify({"ok": True})


@app.post("/api/schedule")
def api_schedule():
    """Either {"paste": "<ESPN schedule page text>"} or {"week": 3, "pairs": [["A","B"],...]}"""
    b = request.get_json(force=True)
    m = load_matchups()
    L = load_rosters(); names = [short(k) for k in L["rosters"]]
    if b.get("paste"):
        parsed = parse_schedule_paste(b["paste"], names)
        m.update(parsed)
        save_matchups(m)
        return jsonify({"ok": True, "weeks_parsed": sorted(parsed.keys()), "parsed": parsed})
    if b.get("week") and b.get("pairs"):
        m[int(b["week"])] = b["pairs"]; save_matchups(m)
        return jsonify({"ok": True})
    return jsonify({"error": "nothing to do"}), 400


if __name__ == "__main__":
    print("Season dashboard: http://127.0.0.1:5056   (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=5056, debug=False)
