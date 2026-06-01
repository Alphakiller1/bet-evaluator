"""
Phase B — read the evaluator's calibration / ROI / CLV / sustainability and report
whether the model's edges are real and repeatable. Honest-empty until settled
predictions accrue (forward-accumulating warehouse).

    python -m backtest.analyze_model [--min-n 20]
"""

from __future__ import annotations

import argparse

from backtest import db


def run(min_n: int = 20):
    cal = db.select("v_calibration_buckets", "?order=prob_bucket.asc")
    roi = db.select("v_roi_by_edge_tier", "?order=roi_per_unit.desc")
    clv = db.select("v_clv_beat_rate")
    logged = db.count("model_predictions")

    print(f"\n  MODEL TRACK RECORD  (predictions logged: {logged})")
    if not cal:
        print("  No settled predictions yet. The record fills as you evaluate bets")
        print("  pre-game and games settle. After games finish:")
        print("    python -m backtest.import_outcomes && python -m backtest.settle_predictions\n")
        return

    print(f"\n  CALIBRATION  predicted vs actual win rate (min sample {min_n})")
    print(f"  {'BUCKET':<9}{'N':>5}{'PRED':>8}{'ACTUAL':>8}{'OVERCONF':>10}")
    for c in cal:
        lo, hi = (c["prob_bucket"] - 1) * 5, c["prob_bucket"] * 5
        flag = "" if c["n"] >= min_n else "  (low)"
        print(f"  {lo:>2.0f}-{hi:<5.0f}{c['n']:>5}{c['avg_predicted']*100:>7.1f}%"
              f"{c['actual_win_rate']*100:>7.1f}%{c['overconfidence']*100:>+9.1f}{flag}")

    print(f"\n  ROI BY EDGE x MARKET  at evaluated price (min sample {min_n})")
    print(f"  {'MARKET':<12}{'EDGE':>7}{'N':>5}{'WIN%':>7}{'ROI/u':>9}")
    shown = 0
    for r in roi:
        if r["n"] < min_n:
            continue
        print(f"  {r['market_type']:<12}{r['edge_bucket']*100:>+6.1f}{r['n']:>5}"
              f"{r['win_rate']*100:>6.1f}%{r['roi_per_unit']:>+9.3f}")
        shown += 1
    if shown == 0:
        print("  (no edge bucket has reached the minimum sample yet)")

    print("\n  CLV BEAT-RATE  leading indicator")
    print(f"  {'MARKET':<12}{'N':>5}{'BEAT%':>8}{'AVG CLV':>10}")
    for c in clv:
        if not c["n"]:
            continue
        print(f"  {c['market_type']:<12}{c['n']:>5}{(c['clv_beat_rate'] or 0)*100:>7.1f}%"
              f"{(c['avg_clv'] or 0)*100:>+9.1f}")
    print()


def main():
    p = argparse.ArgumentParser(description="Model calibration / ROI / CLV analysis.")
    p.add_argument("--min-n", type=int, default=20, help="Min sample to trust a row")
    run(p.parse_args().min_n)


if __name__ == "__main__":
    main()
