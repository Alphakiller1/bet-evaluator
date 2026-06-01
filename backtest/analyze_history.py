"""
Analyze PAST data (outcome-based) from Supabase — kept separate from the forward
sharp track record. Past odds are paid-only, so past analysis is outcomes only:
base rates + per-team records that feed/validate the model anchors.

    python -m backtest.analyze_history [--top 8]
"""

from __future__ import annotations

import argparse

from backtest import db


def run(top: int = 8):
    base = db.select("v_outcome_base_rates")
    if not base or not base[0]["games"]:
        print("\n  No historical outcomes yet. Run: python -m backtest.import_history\n")
        return
    b = base[0]
    print(f"\n  PAST OUTCOMES — {b['games']} final games")
    print(f"    home win rate   : {b['home_win_rate']*100:.1f}%")
    print(f"    avg total runs  : {b['avg_total_runs']}  (sd {b['sd_total_runs']})")
    print(f"    avg margin (home): {b['avg_margin_home']:+}")
    print(f"    avg runs home/away: {b['avg_home_runs']} / {b['avg_away_runs']}")

    teams = db.select("v_team_outcome_perf", "?order=home_win_rate.desc")
    teams = [t for t in teams if t["home_win_rate"] is not None]
    print(f"\n  TOP {top} HOME TEAMS (win rate):")
    for t in teams[:top]:
        print(f"    {t['team']:<5} home {t['home_win_rate']*100:>5.1f}% ({t['home_g']}g)  "
              f"away {(t['away_win_rate'] or 0)*100:>5.1f}% ({t['away_g']}g)")

    print("\n  FUTURE: the sharp track record (forward-only) -> backtest.analyze_sharp")
    print("  (past odds are paid-only on The Odds API, so sharp success can't be backfilled)\n")


def main():
    p = argparse.ArgumentParser(description="Past outcome analysis.")
    p.add_argument("--top", type=int, default=8)
    run(p.parse_args().top)


if __name__ == "__main__":
    main()
