"""
Phase 3 — ingest scraped market odds into Supabase as timestamped snapshots.

Reads the scraped odds store (market_data's odds_latest.csv) and writes rows into
odds_snapshots with snapshot_time = the actual fetch time (so the look-ahead-safe
view v_odds_pregame keeps only odds captured before first pitch). Run after each
`python market_data.py --fetch[-game]` to bank market movement + closing lines.

    python -m backtest.import_odds
"""

from __future__ import annotations

import pandas as pd

import config
from bet_evaluator import american_to_decimal, american_to_implied, load
from backtest import db
from backtest.import_snapshots import game_pk, TODAY


def run():
    if not config.ODDS_LATEST_CSV.exists():
        raise SystemExit("No odds snapshot yet. Run: python market_data.py --fetch")
    df = pd.read_csv(config.ODDS_LATEST_CSV, dtype=str).fillna("")

    m = load("today_matchups.csv")
    matchup_set = set()
    if m is not None:
        matchup_set = set(zip(m["Away"].astype(str).str.upper().str.strip(),
                              m["Home"].astype(str).str.upper().str.strip()))

    rows = []
    for _, r in df.iterrows():
        away, home = r["away"].upper(), r["home"].upper()
        if matchup_set and (away, home) not in matchup_set:
            continue  # only games we have a games row for (FK)
        try:
            odds = int(float(r["odds"]))
        except (ValueError, TypeError):
            continue
        line = None
        if r["line"] not in ("", "nan"):
            try:
                line = float(r["line"])
            except ValueError:
                line = None
        rows.append({
            "game_pk": game_pk(TODAY, away, home),
            "snapshot_time": r["fetched_at"],
            "sportsbook": r["book"],
            "market_type": r["market"],
            "period": "full_game",
            "selection": r["side"],
            "line": line,
            "american_odds": odds,
            "decimal_odds": round(american_to_decimal(odds), 4),
            "implied_probability": round(american_to_implied(odds), 4),
            "is_best_price": None,
            "source": "the-odds-api",
        })

    # Flag best price per (game, market, selection, line).
    best = {}
    for i, r in enumerate(rows):
        key = (r["game_pk"], r["market_type"], r["selection"], r["line"])
        if key not in best or r["american_odds"] > rows[best[key]]["american_odds"]:
            best[key] = i
    for r in rows:
        r["is_best_price"] = False
    for i in best.values():
        rows[i]["is_best_price"] = True

    n = db.insert("odds_snapshots", rows)
    print(f"  Inserted {n} odds rows ({len(best)} best-price flags) "
          f"across {len({r['game_pk'] for r in rows})} games.")
    print(f"  odds_snapshots total: {db.count('odds_snapshots')} · "
          f"pregame: {db.count('v_odds_pregame')}")


if __name__ == "__main__":
    run()
