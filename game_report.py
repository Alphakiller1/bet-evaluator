"""
GAME REPORT — one game, evaluated across markets: Moneyline, Total Runs, First 5 (F5),
and Pitcher Strikeout props. Combines the expected-runs model with the live market
signals (sharp divergence/steam, cross-venue) we have for the game.

    python game_report.py --game CHW@MIN
    python game_report.py --game CHW@MIN --k-line 5.5   # set the pitcher-K prop line

Reads today's matchup/metric CSVs (pipeline) + the Supabase signals. Pre-req: run the
pipeline (real slate) and, for the live signals, sharp_tracker.py earlier today.
"""

from __future__ import annotations

import argparse

import bet_evaluator as be
from bet_evaluator import load, _num

try:
    from backtest import db
    from backtest.import_snapshots import game_pk, TODAY
except Exception:
    db = None

F5_SHARE = 0.55      # first 5 innings ~ 55% of a game's runs (SP-driven, ~5/9 of innings)
PA_PER_INNING = 4.3  # batters faced per inning incl. baserunners


def _sharp_for_game(gpk: int) -> dict:
    if db is None:
        return {}
    try:
        rows = db.select("sharp_signals",
                         f"?game_pk=eq.{gpk}&select=market_type,selection,divergence,steam_flag,"
                         "sharp_novig_prob,soft_novig_prob,snapshot_time&order=snapshot_time.desc")
    except SystemExit:
        return {}
    out = {}
    for r in rows:
        out.setdefault((r["market_type"], r["selection"]), r)
    return out


def _total_line(gpk: int) -> float | None:
    if db is None:
        return None
    try:
        rows = db.select("odds_snapshots",
                         f"?game_pk=eq.{gpk}&market_type=eq.total&selection=eq.over&select=line&limit=20")
    except SystemExit:
        return None
    lines = [r["line"] for r in rows if r.get("line") is not None]
    return round(sorted(lines)[len(lines)//2], 1) if lines else None


def _ip_per_start(team: str) -> float:
    tp = load("team_profiles.csv")
    if tp is not None and "team" in tp.columns:
        tp["team"] = tp["team"].astype(str).str.upper().str.strip()
        row = tp[tp["team"] == team]
        if not row.empty:
            ip = _num(row.iloc[0].get("avg_ip_per_start"))
            if ip and ip > 0:
                return ip * 9 if ip < 1.5 else ip   # handle 0–1 normalised
    return 5.3


def _f5_winpct(team: str) -> float | None:
    tr = load("team_results.csv")
    if tr is not None and "team" in tr.columns:
        tr["team"] = tr["team"].astype(str).str.upper().str.strip()
        row = tr[tr["team"] == team]
        if not row.empty:
            return _num(row.iloc[0].get("f5_win_pct"))
    return None


def _proj_ks(k_pct: float | None, ip: float) -> float | None:
    if k_pct is None:
        return None
    return round(k_pct / 100.0 * ip * PA_PER_INNING, 1)


def _f(x, d: int = 1) -> str:
    return f"{x:.{d}f}" if isinstance(x, (int, float)) else "-"


def _lean(model_p: float, line_label: str) -> str:
    if model_p >= 0.56:
        return f"LEAN {line_label} (model {model_p*100:.0f}%)"
    if model_p <= 0.44:
        return f"LEAN other side (model {model_p*100:.0f}%)"
    return f"pass / no edge (model {model_p*100:.0f}%)"


def run(away: str, home: str, k_line: float = 5.5):
    gd = be.load_game(away, home)
    anchors = be.refresh_anchors()
    probs = be.model_probabilities(gd, anchors)
    gpk = game_pk(TODAY, away, home) if db is not None else None
    sharp = _sharp_for_game(gpk) if gpk else {}

    print(f"\n  ===== GAME REPORT - {away} @ {home} =====")
    print(f"  SP:  {away} {gd.away_sp} ({gd.away_hand}, FIP {_f(gd.away_fip,2)}, K% {_f(gd.away_k)}, HR9 {_f(gd.away_hr9,2)})")
    print(f"       {home} {gd.home_sp} ({gd.home_hand}, FIP {_f(gd.home_fip,2)}, K% {_f(gd.home_k)}, HR9 {_f(gd.home_hr9,2)})")
    print(f"  Lineup OSI: {away} {gd.away_osi} vs {home} {gd.home_osi} | Park {gd.park_factor}")

    # ── MONEYLINE ────────────────────────────────────────────────────────────
    print("\n  -- MONEYLINE --")
    print(f"  Model: {away} {probs.p_away_win*100:.0f}% / {home} {probs.p_home_win*100:.0f}%")
    for sidekey, who in (((("ml", away)), away), ((("ml", home)), home)):
        s = sharp.get(sidekey)
        if s:
            steam = " STEAM" if s.get("steam_flag") else ""
            print(f"  Sharp: {who} +{(s['divergence'] or 0)*100:.1f} "
                  f"(sharp {(s['sharp_novig_prob'] or 0)*100:.0f}% vs soft {(s['soft_novig_prob'] or 0)*100:.0f}%){steam}")

    # ── TOTAL RUNS ───────────────────────────────────────────────────────────
    print("\n  -- TOTAL RUNS --")
    line = _total_line(gpk) if gpk else None
    L = line if line else round(probs.exp_total * 2) / 2
    p_over, _ = be.market_probability("total", "over", L, gd, probs, anchors, None)
    print(f"  Model expected total: {probs.exp_total:.2f} runs  ({away} {probs.exp_away_runs:.2f} / {home} {probs.exp_home_runs:.2f})")
    print(f"  Market line: {('%.1f' % line) if line else 'n/a (using %.1f)' % L}  ->  {_lean(p_over, f'OVER {L}')}")
    st = sharp.get(("total", "over")) or sharp.get(("total", "under"))
    if st:
        steam = " STEAM" if st.get("steam_flag") else ""
        print(f"  Sharp total: {st['selection']} +{(st['divergence'] or 0)*100:.1f}{steam}")

    # ── FIRST 5 (F5) ─────────────────────────────────────────────────────────
    print("\n  -- FIRST 5 INNINGS (F5) --")
    f5_total = probs.exp_total * F5_SHARE
    f5_line = round(f5_total * 2) / 2
    p_f5_over, _ = be.market_probability("total", "over", f5_line / F5_SHARE, gd, probs, anchors, None)
    print(f"  Model F5 total est: {f5_total:.2f} runs  ->  {_lean(p_f5_over, f'OVER {f5_line}')}")
    fa, fh = _f5_winpct(away), _f5_winpct(home)
    if fa is not None or fh is not None:
        print(f"  Pipeline F5 win%: {away} {('%.0f%%'%(fa*100)) if fa is not None else '-'} / "
              f"{home} {('%.0f%%'%(fh*100)) if fh is not None else '-'}  (SP/early-game strength)")
    print("  (Live F5 line on Kalshi KXMLBF5TOTAL - compare the model est to that number.)")

    # ── PITCHER STRIKEOUTS ───────────────────────────────────────────────────
    print("\n  -- PITCHER STRIKEOUTS (props) --")
    for who, sp, kp, opp in ((away, gd.away_sp, gd.away_k, home), (home, gd.home_sp, gd.home_k, away)):
        ip = _ip_per_start(who)
        ks = _proj_ks(kp, ip)
        if ks is None:
            print(f"  {sp} ({who}): K% n/a - can't project")
            continue
        edge = ks - k_line
        lean = "OVER" if edge >= 0.4 else ("UNDER" if edge <= -0.4 else "pass")
        print(f"  {sp} ({who}): K% {_f(kp)} x {ip:.1f} IP -> ~{ks} Ks vs line {k_line}  ->  {lean} ({edge:+.1f})")
    print("  (Live K lines on Kalshi KXMLBKS / sportsbook props - use ~5.5 default or pass --k-line.)\n")


def main():
    p = argparse.ArgumentParser(description="Per-game report: ML / Total / F5 / Pitcher Ks.")
    p.add_argument("--game", required=True, help='"AWAY@HOME", e.g. CHW@MIN')
    p.add_argument("--k-line", type=float, default=5.5, help="Pitcher strikeout prop line")
    a = p.parse_args()
    away, home = (s.strip().upper() for s in a.game.split("@", 1))
    run(away, home, a.k_line)


if __name__ == "__main__":
    main()
