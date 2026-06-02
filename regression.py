"""
Regression / progression detector - pitching is biomechanical, so a starter who has
been over-performing his baseline is fatiguing toward the mean (regression), and one
under-performing tends to bounce back (progression). The edge appears when the MARKET
line chases the recent streak (recency bias) and so over- or under-represents the
pitcher's true baseline.

  REGRESSION (hot -> fade): recent starts well above baseline -> expect pullback.
    If the K line / his projection is elevated to match the streak -> UNDER value.
  PROGRESSION (cold -> back): recent starts below baseline -> expect bounce-back.
    If the line is depressed to match the slump -> OVER value.

Uses per-start logs (sp_gamelog.csv): recent 3 starts vs season baseline for game
score (start quality) and strikeouts, cross-referenced with the live Kalshi K line.

    python regression.py                 # scan today's starters
    python regression.py --game LAD@ARI  # one game
"""

from __future__ import annotations

import argparse

from bet_evaluator import load, _num

GS_DEV = 10.0   # game-score deviation (recent vs season) to call hot/cold
K_DEV = 1.5     # strikeout deviation
RECENT_N = 3


def pitcher_form(name: str, n_recent: int = RECENT_N) -> dict | None:
    gl = load("sp_gamelog.csv")
    if gl is None or "pitcher_name" not in gl.columns or not name or name == "TBD":
        return None
    gl = gl.copy()
    gl["pitcher_name"] = gl["pitcher_name"].astype(str)
    exact = gl[gl["pitcher_name"].str.lower() == name.lower()]
    g = exact if not exact.empty else gl[
        gl["pitcher_name"].str.lower().str.contains(name.split()[-1].lower(), na=False)]
    if g.empty or len(g) < 4:
        return None
    g = g.sort_values("date")
    recent = g.tail(n_recent)

    def avg(df, c):
        return float(df[c].mean()) if c in df.columns else None
    return {
        "name": name, "starts": len(g),
        "recent_k": avg(recent, "K"), "season_k": avg(g, "K"),
        "recent_gs": avg(recent, "game_score"), "season_gs": avg(g, "game_score"),
        "recent_er": avg(recent, "ER"), "season_er": avg(g, "ER"),
    }


def regression_signal(form: dict | None):
    """Return (state, detail, k_dev) - REGRESSION / PROGRESSION / STABLE."""
    if not form or form.get("recent_gs") is None or form.get("season_gs") is None:
        return None
    gs_dev = form["recent_gs"] - form["season_gs"]
    k_dev = (form["recent_k"] or 0) - (form["season_k"] or 0)
    base = (f"GS {form['recent_gs']:.0f} vs {form['season_gs']:.0f}, "
            f"K {form['recent_k']:.1f} vs {form['season_k']:.1f} (last {RECENT_N} of {form['starts']})")
    if gs_dev >= GS_DEV or k_dev >= K_DEV:
        return ("REGRESSION", f"hot, fading toward mean - {base}", k_dev)
    if gs_dev <= -GS_DEV or k_dev <= -K_DEV:
        return ("PROGRESSION", f"cold, due to bounce back - {base}", k_dev)
    return ("STABLE", f"near baseline - {base}", k_dev)


def tag(name: str) -> str | None:
    """Short one-line form tag for game_report."""
    sig = regression_signal(pitcher_form(name))
    if not sig or sig[0] == "STABLE":
        return None
    state, detail, _ = sig
    lean = "lean UNDER his Ks if the line chased the streak" if state == "REGRESSION" \
        else "lean OVER his Ks if the line discounts him"
    return f"{state} - {detail}; {lean}"


def run(only_game: str | None = None):
    m = load("today_matchups.csv")
    if m is None:
        raise SystemExit("today_matchups.csv not found - run the pipeline.")
    m["Away"] = m["Away"].astype(str).str.upper().str.strip()
    m["Home"] = m["Home"].astype(str).str.upper().str.strip()

    try:
        from backtest import prediction_markets as pm
        from backtest.import_snapshots import TODAY
    except Exception:
        pm = None

    print("\n  REGRESSION / PROGRESSION BOARD (recent 3 starts vs season baseline)")
    print(f"  {'PITCHER':<20}{'TEAM':<5}{'STATE':<13}{'K LINE':>7}  NOTE")
    found = False
    for _, g in m.iterrows():
        away, home = g["Away"], g["Home"]
        if only_game and f"{away}@{home}" != only_game.upper():
            continue
        ks = {}
        if pm is not None:
            try:
                ks = pm.ks_market(away, home, TODAY)
            except Exception:
                ks = {}
        for who, sp in ((away, str(g.get("Away_SP", "TBD"))), (home, str(g.get("Home_SP", "TBD")))):
            sig = regression_signal(pitcher_form(sp))
            if not sig or sig[0] == "STABLE":
                continue
            found = True
            state, detail, k_dev = sig
            # live K line (balanced strike) for cross-reference
            kline = "-"
            value = ""
            ladder = next((v for n, v in ks.items() if sp.split()[-1].lower() in n.lower()), None) if ks else None
            if ladder:
                mline = min(ladder, key=lambda k: abs(ladder[k] - 0.5))
                kline = f"{mline}"
                base_k = pitcher_form(sp)["season_k"]
                if state == "REGRESSION" and mline >= base_k:
                    value = "  ** UNDER value (line >= baseline, fading hot streak)"
                elif state == "PROGRESSION" and mline <= base_k:
                    value = "  ** OVER value (line <= baseline, discounting slump)"
            print(f"  {sp[:19]:<20}{who:<5}{state:<13}{kline:>7}  {detail}{value}")
    if not found:
        print("  No pitcher far enough from baseline today (or insufficient gamelog).")
    print("\n  REGRESSION = over-performing recent form -> expect pullback (biological mean")
    print("  reversion). PROGRESSION = under-performing -> bounce-back. ** = the market line")
    print("  appears to chase the streak, so it over/under-represents the baseline = value.\n")


def main():
    p = argparse.ArgumentParser(description="Pitcher regression / progression detector.")
    p.add_argument("--game", help='Limit to "AWAY@HOME"')
    run(p.parse_args().game)


if __name__ == "__main__":
    main()
