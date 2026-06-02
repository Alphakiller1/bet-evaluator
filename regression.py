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


def _ip_true(x) -> float:
    """Box-score IP (5.2 = 5 and 2/3) -> true innings."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    whole = int(f)
    return whole + round((f - whole) * 10) / 3.0


FIP_CONST = 3.10   # league FIP constant (approx); only the FIP-vs-ERA GAP matters here
LUCK_GAP = 0.75    # |recent FIP - recent ERA| to call results luck-driven
HEAVY_PITCHES = 100  # recent avg pitch count flagging workload/fatigue


def _agg(df) -> dict:
    ip = sum(_ip_true(v) for v in df["IP"]) if "IP" in df.columns else 0.0
    if ip <= 0:
        return {}
    s = lambda c: float(df[c].sum()) if c in df.columns else 0.0
    fip = (13 * s("HR") + 3 * s("BB") - 2 * s("K")) / ip + FIP_CONST
    era = 9 * s("ER") / ip
    return {"fip": round(fip, 2), "era": round(era, 2),
            "k9": round(9 * s("K") / ip, 1),
            "pitches": round(df["pitches"].mean(), 0) if "pitches" in df.columns else None}


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
    ra, se = _agg(recent), _agg(g)
    return {
        "name": name, "starts": len(g),
        "recent_k": avg(recent, "K"), "season_k": avg(g, "K"),
        "recent_gs": avg(recent, "game_score"), "season_gs": avg(g, "game_score"),
        "recent_fip": ra.get("fip"), "recent_era": ra.get("era"),
        "season_fip": se.get("fip"), "season_era": se.get("era"),
        "recent_pitches": ra.get("pitches"),
        # luck gap: FIP - ERA. positive => ERA below peripherals => lucky (regress).
        "luck": round((ra.get("fip") - ra.get("era")), 2) if ra.get("fip") is not None and ra.get("era") is not None else None,
    }


def regression_signal(form: dict | None):
    """Return (state, detail, k_dev) - REGRESSION / PROGRESSION / STABLE.
    Research-grounded: the regression fuel is luck (recent ERA vs FIP/peripherals;
    BABIP/HR-FB/LOB% don't stabilize), corroborated by game-score swing. K% is semi-
    stable (~70 BF) so it's a secondary tell. Heavy recent workload adds fatigue risk."""
    if not form or form.get("recent_gs") is None or form.get("season_gs") is None:
        return None
    gs_dev = form["recent_gs"] - form["season_gs"]
    k_dev = (form["recent_k"] or 0) - (form["season_k"] or 0)
    luck = form.get("luck")          # +FIP>ERA = lucky (regress) ; -ERA>FIP = unlucky
    heavy = form.get("recent_pitches") and form["recent_pitches"] >= HEAVY_PITCHES
    base = (f"ERA {form['recent_era']} vs FIP {form['recent_fip']} (luck {luck:+}), "
            f"GS {form['recent_gs']:.0f} vs {form['season_gs']:.0f}, "
            f"K {form['recent_k']:.1f} vs {form['season_k']:.1f} [last {RECENT_N}/{form['starts']}]"
            if luck is not None else
            f"GS {form['recent_gs']:.0f} vs {form['season_gs']:.0f} [last {RECENT_N}/{form['starts']}]")
    fatigue = " + heavy workload (fatigue risk)" if heavy else ""

    hot = (luck is not None and luck >= LUCK_GAP) or gs_dev >= GS_DEV or k_dev >= K_DEV
    cold = (luck is not None and luck <= -LUCK_GAP) or gs_dev <= -GS_DEV or k_dev <= -K_DEV
    if hot:
        return ("REGRESSION", f"hot/lucky, mean-reverting - {base}{fatigue}", k_dev)
    if cold:
        return ("PROGRESSION", f"cold/unlucky, due to bounce back - {base}", k_dev)
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
