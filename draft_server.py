#!/usr/bin/env python
"""Part 3: live draft board. Run `python draft_server.py` and open http://127.0.0.1:5055"""
import json
import math
import re
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request
from rapidfuzz import fuzz, process

from ffdraft.config import ROOT, DATA, load_settings
from ffdraft.names import norm_name
from ffdraft.opponents import simulate

app = Flask(__name__, template_folder=str(ROOT / "templates"))
STATE_FILE = DATA / "draft_state.json"
ADP_SD = 8.0  # fallback noise around ADP when no slot is set
VONA_WEIGHT = 0.5  # how much "value lost by waiting" moves the score

S = load_settings()
T = S["num_teams"]
ROUNDS = S["roster_size"]
LINEUP = S["starting_lineup"]
MIN_RB, MIN_WR = S["min_rb"], S["min_wr"]
MAX_POS = {"QB": 4, "RB": 8, "WR": 8, "TE": 3, "K": 3, "DST": 3}
POS_LABEL = {"QB": "quarterback", "RB": "running back", "WR": "wide receiver", "TE": "tight end", "K": "kicker", "DST": "defense"}

BOARD = pd.read_csv(ROOT / "board.csv")
BOARD["team"] = BOARD["team"].fillna("")
BOARD["bye"] = BOARD["bye"].fillna(0).astype(int)
BOARD = BOARD.set_index("key", drop=False)
def _initials(name):
    parts = [t for t in re.split(r"[\s\-]+", re.sub(r"[.']", "", name)) if t and t.lower() not in ("jr", "sr", "ii", "iii", "iv")]
    return "".join(t[0].lower() for t in parts)
BOARD["initials"] = BOARD["player"].map(_initials)
NICKS = {"cmc": "christianmccaffrey", "jsn": "jaxonsmithnjigba", "arsb": "amonrastbrown", "jt": "jonathantaylor",
         "cd": "ceedeelamb", "jj": "justinjefferson", "aj": "ajbrown", "djm": "djmoore", "mhj": "marvinharrison",
         "bijan": "bijanrobinson", "puka": "pukanacua", "saquon": "saquonbarkley", "tmac": "tetairoamcmillan",
         "ladd": "laddmcconkey", "bowers": "brockbowers", "kelce": "traviskelce", "kittle": "georgekittle"}

# ---------------------------------------------------------------- state
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"my_slot": S.get("my_draft_slot"), "picks": [], "bots": []}


def save_state(st):
    STATE_FILE.write_text(json.dumps(st, indent=1))


STATE = load_state()
STATE.setdefault("bots", [])
STATE.setdefault("names", {})


# ---------------------------------------------------------------- draft math
def slot_for_pick(p):
    r = (p - 1) // T + 1
    i = (p - 1) % T
    return (i + 1) if r % 2 == 1 else (T - i), r


def my_pick_numbers(slot):
    out = []
    for r in range(1, ROUNDS + 1):
        i = slot - 1 if r % 2 == 1 else T - slot
        out.append((r - 1) * T + i + 1)
    return out


def p_available(adp, at_pick):
    """P(player still on the board when pick `at_pick` comes) assuming he goes ~N(adp, ADP_SD)."""
    if adp is None or (isinstance(adp, float) and math.isnan(adp)):
        return 0.95
    z = (at_pick - adp) / ADP_SD
    return max(0.0, min(1.0, 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))))


def roster_counts(mine):
    c = {p: 0 for p in MAX_POS}
    for r in mine:
        c[r["pos"]] = c.get(r["pos"], 0) + 1
    return c


def eligibility(pos, counts, my_picks_left, next_round):
    """Return (eligible: bool, reason: str) for taking `pos` with my next pick."""
    have = counts.get(pos, 0)
    if have >= MAX_POS.get(pos, 99):
        return False, f"ESPN roster max for {pos} reached"
    missing = {p: int(counts.get(p, 0) == 0) for p in ["QB", "TE", "K", "DST"]}
    n_missing = sum(missing.values())
    if pos in ("K", "DST"):
        if have >= 1:
            return False, f"Already have a {POS_LABEL[pos]}. One is all you start."
        if next_round < ROUNDS - 1:
            return False, "Kickers and defenses only in the last two rounds. No exceptions."
        return True, ""
    if pos in ("QB", "TE"):
        if have >= 1:
            return False, f"Already have a {POS_LABEL[pos]}. Every other pick is RB or WR."
        return True, ""
    # RB / WR: make sure enough picks remain for mandatory slots + depth minimums
    need_rb = max(0, MIN_RB - counts.get("RB", 0)) - (1 if pos == "RB" else 0)
    need_wr = max(0, MIN_WR - counts.get("WR", 0)) - (1 if pos == "WR" else 0)
    need_after = n_missing + max(0, need_rb) + max(0, need_wr)
    if my_picks_left - 1 < need_after:
        other = "WR" if pos == "RB" else "RB"
        return False, f"Not enough picks left to still reach {MIN_RB} RB / {MIN_WR} WR plus QB/TE/K/DST. Take a {other} or a required position."
    return True, ""


def need_bonus(pos, counts, next_round):
    rb, wr = counts.get("RB", 0), counts.get("WR", 0)
    flex_open = (rb + wr) < (LINEUP["RB"] + LINEUP["WR"] + LINEUP["FLEX"])
    if pos == "RB":
        if rb < LINEUP["RB"]: return 12, "You still need a starting RB."
        if flex_open: return 6, "Your FLEX slot is still open."
        if rb < MIN_RB: return 4, f"Building toward {MIN_RB} RBs for injury cover ({rb} so far)."
        return -8, f"You already have {rb} RBs; depth here is less valuable now."
    if pos == "WR":
        if wr < LINEUP["WR"]: return 12, "You still need a starting WR."
        if flex_open: return 6, "Your FLEX slot is still open."
        if wr < MIN_WR: return 4, f"Building toward {MIN_WR} WRs for injury cover ({wr} so far)."
        return -8, f"You already have {wr} WRs; depth here is less valuable now."
    if pos in ("QB", "TE"):
        if next_round >= 10: return 15, f"Round {next_round}: you can't leave the draft without a {POS_LABEL[pos]}."
        if next_round >= 7: return 6, f"Mid-draft is a fine time to grab your one {POS_LABEL[pos]}."
        return 0, ""
    if pos in ("K", "DST"):
        return 10, "Required slot; last two rounds."
    return 0, ""


def build_recs(avail, mine, next_pick, my_picks_left, sim=None):
    sim = sim or {}
    counts = roster_counts(mine)
    _, next_round = slot_for_pick(min(next_pick, T * ROUNDS))
    my_teams = {r["team"] for r in mine if r["pos"] in ("QB", "RB", "WR", "TE") and r["team"]}
    my_byes = [r["bye"] for r in mine if r["pos"] in ("QB", "RB", "WR", "TE") and r["bye"]]
    the_pick_after = None
    if STATE["my_slot"]:
        later = [p for p in my_pick_numbers(STATE["my_slot"]) if p > next_pick]
        the_pick_after = later[0] if later else None

    rows = []
    by_pos = {pos: g.sort_values("vbd", ascending=False) for pos, g in avail.groupby("pos")}
    # only the top of the board can be a sensible pick; K/DST kept so the last rounds still work
    per_pos = {"QB": 16, "RB": 45, "WR": 50, "TE": 16, "K": 14, "DST": 14}
    top = pd.concat([g.head(per_pos.get(pos, 20)) for pos, g in by_pos.items()])
    for key, r in top.iterrows():
        pos = r["pos"]
        elig, why_not = eligibility(pos, counts, my_picks_left, next_round)
        bonus, bonus_txt = need_bonus(pos, counts, next_round)
        vbd = float(r["vbd"])
        same_team = r["team"] in my_teams and pos in ("QB", "RB", "WR", "TE")
        st_pen = 0.08 * max(vbd, 0) if same_team else 0.0
        n_bye = sum(1 for b in my_byes if b == r["bye"]) + 1 if r["bye"] else 0
        bye_pen = 8.0 * (n_bye - 2) ** 2 if n_bye >= 3 else 0.0
        score = vbd + bonus - st_pen - bye_pen
        adp = None if pd.isna(r["adp_avg"]) else float(r["adp_avg"])
        if sim.get("surv_now") is not None:
            p_now = sim["surv_now"].get(key, 1.0)
            p_next = sim["surv_next"].get(key, 1.0) if sim.get("surv_next") is not None else 0.0
        else:
            p_now = p_available(adp, next_pick)
            p_next = p_available(adp, the_pick_after) if the_pick_after else 0.0
        exp_best = (sim.get("exp_best_next") or {}).get(pos)
        decay = max(0.0, vbd - exp_best) if exp_best is not None and not (isinstance(exp_best, float) and math.isnan(exp_best)) else 0.0
        if elig:
            score += VONA_WEIGHT * decay
        # same-position tier context
        same_pos = by_pos[pos]
        tier_left = int((same_pos["pos_tier"] == r["pos_tier"]).sum())
        nxt = same_pos[same_pos["vbd"] < vbd].head(1)
        gap_next = float(vbd - nxt["vbd"].iloc[0]) if len(nxt) else 0.0
        next_name = nxt["player"].iloc[0] if len(nxt) else None
        next_tier = same_pos[same_pos["pos_tier"] > r["pos_tier"]].head(1)
        gap_tier = float(vbd - next_tier["vbd"].iloc[0]) if len(next_tier) else 0.0

        why = []
        why.append(f"{POS_LABEL[pos].title()} #{int(r['pos_rank'])} on the board, tier {int(r['pos_tier'])}. {tier_left} player{'s' if tier_left != 1 else ''} left in this tier.")
        if gap_tier >= 8:
            why.append(f"The next {pos} tier starts {gap_tier:.0f} points lower. That's a real drop-off.")
        elif gap_tier > 0 and next_name:
            why.append(f"Next {pos} tier starts only {gap_tier:.0f} points lower ({next_name}), so similar value is still available.")
        elif next_name:
            why.append(f"Next {pos} after him is {next_name}, {gap_next:.0f} points lower. Near-equivalent, so no need to panic.")
        if the_pick_after and sim.get("surv_next") is not None:
            why.append(f"Simulating the {sim['n_between_next']} picks before your following pick (#{the_pick_after}) using each team's roster: about {p_next*100:.0f}% chance he's still there. " + ("Take him now if you want him." if p_next < 0.5 else "You could probably wait a round."))
        elif adp is not None and the_pick_after:
            why.append(f"Drafts usually take him around pick {adp:.0f}. Your following pick is #{the_pick_after}: about {p_next*100:.0f}% chance he'd still be there. " + ("Take him now if you want him." if p_next < 0.5 else "You could probably wait a round."))
        if exp_best is not None and not math.isnan(exp_best) and the_pick_after:
            if decay > 0:
                why.append(f"If you pass, the best {pos} expected at your next turn is worth about {exp_best:.0f}. Taking him now gains {decay:.0f} at the position.")
            else:
                why.append(f"A {pos} of about the same value ({exp_best:.0f}) should still be there at your next turn, so there's no rush at this position.")
        if bonus_txt: why.append(bonus_txt)
        why.append(f"Durability: expected {r['exp_games']:.1f} of 17 games based on the last 3 seasons ({'rookie / no history, positional average' if pd.isna(r['hist_seasons']) else f'{int(r['hist_games'])} games in {int(r['hist_seasons'])} seasons'}). Weekly swing about ±{r['weekly_sd']:.0f} points.")
        if bool(r["committee"]): why.append("Shares the backfield with another projected 110+ carry back, so his value is docked 10%.")
        if bool(r["td_dep"]): why.append("Touchdown-dependent: more than 35% of his points come from TDs, which are volatile. Docked 5%.")
        if same_team: why.append(f"You already have a starter from {r['team']}. Stacking one offense raises your bust risk, so −8%.")
        if bye_pen: why.append(f"That would put {n_bye} of your starters on the same bye week ({int(r['bye'])}). Penalized {bye_pen:.0f} points.")
        if not elig: why.insert(0, "NOT ALLOWED: " + why_not)

        rows.append({
            "key": key, "player": r["player"], "team": r["team"], "pos": pos, "bye": int(r["bye"]),
            "pos_rank": int(r["pos_rank"]), "tier": int(r["pos_tier"]), "rank": int(r["rank"]),
            "proj": round(float(r["proj_pts"]), 1), "vbd": round(vbd, 1), "score": round(score, 1),
            "adp": adp, "p_now": round(p_now, 2), "p_next": round(p_next, 2),
            "eligible": elig, "why": why, "decay": round(decay, 1), "flags": {"committee": bool(r["committee"]), "td": bool(r["td_dep"]), "same_team": same_team, "bye_stack": bye_pen > 0},
        })
    rows.sort(key=lambda x: (not x["eligible"], -x["score"]))
    return rows


def available():
    taken = {p["key"] for p in STATE["picks"]}
    return BOARD[~BOARD.index.isin(taken)]


def build_state():
    picks = STATE["picks"]
    next_pick = len(picks) + 1
    slot = STATE["my_slot"]
    total = T * ROUNDS
    done = next_pick > total
    on_clock, rnd = slot_for_pick(min(next_pick, total))
    mine = [p for p in picks if p["mine"]]
    my_nums = my_pick_numbers(slot) if slot else []
    upcoming = [p for p in my_nums if p >= next_pick]
    my_next = upcoming[0] if upcoming else None
    my_picks_left = len(upcoming) if slot else max(1, ROUNDS - len(mine))
    avail = available()
    bots = set(STATE.get("bots", []))
    rosters = {sl: {} for sl in range(1, T + 1)}
    for p in picks:
        rosters.setdefault(p["slot"], {})[p["pos"]] = rosters.setdefault(p["slot"], {}).get(p["pos"], 0) + 1
    sim = {}
    if slot and not done and my_next:
        later = [p for p in my_nums if p > my_next]
        pick_after = later[0] if later else None
        between_now = [(p, slot_for_pick(p)[0]) for p in range(next_pick, my_next)]
        between_next = between_now + ([(p, slot_for_pick(p)[0]) for p in range(my_next + 1, pick_after)] if pick_after else [])
        try:
            surv_now, _, _ = simulate(avail, between_now, rosters, bots, LINEUP, seed=next_pick)
            if pick_after:
                surv_next, exp_best, _ = simulate(avail, between_next, rosters, bots, LINEUP, seed=next_pick + 1)
            else:
                surv_next, exp_best = None, {}
            sim = {"surv_now": surv_now, "surv_next": surv_next, "exp_best_next": exp_best,
                   "n_between_now": len(between_now), "n_between_next": len(between_next), "pick_after": pick_after}
        except Exception as e:  # the fancy layer must never take down the basic board
            print(f"[warn] opponent simulation failed, falling back to ADP odds: {e!r}")
            sim = {}
    recs = build_recs(avail, mine, my_next or next_pick, my_picks_left, sim)
    # opponents summary
    before_me = {sl for _, sl in (sim.get("n_between_now") and [(p, slot_for_pick(p)[0]) for p in range(next_pick, my_next)] or [])}
    opponents = []
    for sl in range(1, T + 1):
        c = rosters.get(sl, {})
        rb, wr = c.get("RB", 0), c.get("WR", 0)
        if sl in bots: need = "ESPN rank"
        elif rb < LINEUP["RB"] and wr < LINEUP["WR"]: need = "RB or WR"
        elif rb < LINEUP["RB"]: need = "RB"
        elif wr < LINEUP["WR"]: need = "WR"
        elif c.get("QB", 0) == 0 and rnd >= 7: need = "QB"
        elif c.get("TE", 0) == 0 and rnd >= 7: need = "TE"
        elif rnd >= 15 and (c.get("K", 0) == 0 or c.get("DST", 0) == 0): need = "K/DST"
        else: need = "depth"
        opponents.append({"slot": sl, "name": STATE.get("names", {}).get(str(sl), ""), "is_bot": sl in bots, "is_me": sl == slot, "counts": {p: c.get(p, 0) for p in MAX_POS},
                          "n": sum(c.values()), "picks_before_me": sl in before_me, "need": need,
                          "players": [p["player"] for p in picks if p["slot"] == sl]})
    # cost-of-waiting table
    vona = []
    if sim.get("exp_best_next"):
        for pos in ["RB", "WR", "QB", "TE"]:
            now = avail[avail["pos"] == pos]["vbd"].max()
            nxt = sim["exp_best_next"].get(pos)
            if nxt is not None and not math.isnan(nxt):
                vona.append({"pos": pos, "best_now": round(float(now), 1), "best_next": round(float(nxt), 1), "cost": round(float(now - nxt), 1)})
    counts = roster_counts(mine)
    needs = [
        {"pos": "QB", "have": counts["QB"], "need": LINEUP["QB"], "label": "Quarterback"},
        {"pos": "RB", "have": counts["RB"], "need": MIN_RB, "label": f"Running backs (start {LINEUP['RB']}, want {MIN_RB})"},
        {"pos": "WR", "have": counts["WR"], "need": MIN_WR, "label": f"Wide receivers (start {LINEUP['WR']}, want {MIN_WR})"},
        {"pos": "TE", "have": counts["TE"], "need": LINEUP["TE"], "label": "Tight end"},
        {"pos": "K", "have": counts["K"], "need": LINEUP["K"], "label": "Kicker (last 2 rounds)"},
        {"pos": "DST", "have": counts["DST"], "need": LINEUP["DEF"], "label": "Defense (last 2 rounds)"},
    ]
    return {
        "league": {"teams": T, "rounds": ROUNDS, "scoring": S["scoring"], "name": S.get("league_name", "")},
        "my_slot": slot, "next_pick": next_pick, "round": rnd, "on_clock": on_clock, "done": done,
        "my_turn": (slot is not None and on_clock == slot and not done),
        "my_next_pick": my_next, "picks_until_mine": (my_next - next_pick) if my_next else None,
        "my_pick_numbers": my_nums, "my_picks_left": my_picks_left,
        "roster": mine, "needs": needs, "bots": sorted(bots), "opponents": opponents, "vona": vona,
        "sim_note": (f"Simulated {sim['n_between_next']} picks to your following pick #{sim['pick_after']}" if sim.get("pick_after") else ""),
        "recs": recs[:60],
        "log": list(reversed(picks))[:40],
        "n_available": int(len(avail)),
    }


# ---------------------------------------------------------------- routes
@app.errorhandler(Exception)
def _any_error(e):
    import traceback; traceback.print_exc()
    return jsonify({"error": f"Server error: {type(e).__name__}: {e}. Your picks are saved; try Undo or reload."}), 500

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    return jsonify(build_state())


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    avail = available()
    names = {k: f"{r['player']} {r['team']} {r['pos']}" for k, r in avail.iterrows()}
    hits = process.extract(q, names, scorer=fuzz.WRatio, limit=12)
    ql = re.sub(r"[^a-z]", "", q.lower())
    boosted = []
    if ql in NICKS and NICKS[ql] in avail.index:
        boosted.append(NICKS[ql])
    if len(ql) <= 4:
        boosted += [k for k in avail.index[avail["initials"] == ql] if k not in boosted]
    hits = [(None, 100, k) for k in boosted] + [h for h in hits if h[2] not in boosted]
    out = []
    for _, score, key in hits:
        r = avail.loc[key]
        out.append({"key": key, "player": r["player"], "team": r["team"], "pos": r["pos"], "tier": int(r["pos_tier"]),
                    "rank": int(r["rank"]), "vbd": round(float(r["vbd"]), 1), "match": int(score),
                    "adp": None if pd.isna(r["adp_avg"]) else round(float(r["adp_avg"]), 1)})
    # prefer strong matches, then board rank
    out.sort(key=lambda x: (-(x["match"] // 10), x["rank"]))
    return jsonify(out[:8])


@app.post("/api/pick")
def api_pick():
    body = request.get_json(force=True)
    mine = bool(body.get("mine", False))
    key = body.get("key")
    if key and key in BOARD.index:
        r = BOARD.loc[key]
        rec = {"key": key, "player": r["player"], "team": r["team"], "pos": r["pos"], "bye": int(r["bye"]),
               "tier": int(r["pos_tier"]), "vbd": round(float(r["vbd"]), 1)}
    else:
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"error": "no player"}), 400
        key = "unknown:" + norm_name(name)
        rec = {"key": key, "player": name, "team": body.get("team", ""), "pos": body.get("pos", "WR"), "bye": 0, "tier": 0, "vbd": 0.0}
    if any(p["key"] == key for p in STATE["picks"]):
        return jsonify({"error": f"{rec['player']} is already marked as drafted"}), 400
    rec["mine"] = mine
    rec["pick_no"] = len(STATE["picks"]) + 1
    rec["slot"], rec["round"] = slot_for_pick(rec["pick_no"])
    STATE["picks"].append(rec)
    save_state(STATE)
    return jsonify(build_state())


@app.post("/api/undo")
def api_undo():
    if STATE["picks"]:
        STATE["picks"].pop()
        save_state(STATE)
    return jsonify(build_state())


@app.post("/api/toggle_mine")
def api_toggle_mine():
    key = request.get_json(force=True).get("key")
    for p in STATE["picks"]:
        if p["key"] == key:
            p["mine"] = not p["mine"]
    save_state(STATE)
    return jsonify(build_state())


@app.post("/api/slot")
def api_slot():
    slot = request.get_json(force=True).get("slot")
    STATE["my_slot"] = int(slot) if slot else None
    save_state(STATE)
    try:  # keep settings.json in sync
        sp = ROOT / "settings.json"
        s = json.loads(sp.read_text()); s["my_draft_slot"] = STATE["my_slot"]; sp.write_text(json.dumps(s, indent=2))
    except Exception:
        pass
    return jsonify(build_state())


@app.post("/api/bots")
def api_bots():
    slots = request.get_json(force=True).get("slots", [])
    STATE["bots"] = sorted({int(x) for x in slots if 1 <= int(x) <= T})
    save_state(STATE)
    return jsonify(build_state())


@app.post("/api/names")
def api_names():
    """Set team names by draft slot: {"names": {"1": "Rip Evan Lu", ...}} or {"order": ["name1", ..., "name12"]}."""
    body = request.get_json(force=True)
    if "order" in body:
        STATE["names"] = {str(i + 1): n for i, n in enumerate(body["order"][:T])}
    else:
        STATE["names"].update({str(k): v for k, v in body.get("names", {}).items()})
    save_state(STATE)
    return jsonify(build_state())


@app.post("/api/set_pick_slot")
def api_set_pick_slot():
    body = request.get_json(force=True)
    for p in STATE["picks"]:
        if p["key"] == body.get("key"):
            p["slot"] = int(body["slot"])
            p["mine"] = (STATE["my_slot"] is not None and p["slot"] == STATE["my_slot"])
    save_state(STATE)
    return jsonify(build_state())


@app.post("/api/reset")
def api_reset():
    STATE["picks"] = []
    save_state(STATE)
    return jsonify(build_state())


if __name__ == "__main__":
    print("Draft board: http://127.0.0.1:5055   (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=5055, debug=False)
