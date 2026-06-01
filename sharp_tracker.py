"""
Line movement + sharp money tracker across sharp sportsbooks.

Sharp books (Pinnacle, BetOnline, LowVig, Bookmaker, Circa) move first and run
thin margins; the rest are public/soft. Without public betting %, the implementable
sharp signals are:

  1. Sharp-vs-soft divergence — de-vig each book's two-way market to a true
     probability, compare the SHARP consensus to the SOFT consensus. When sharps
     price a side higher than the public books, that's sharp lean.
  2. Line movement — consensus open -> current per selection.
  3. Steam — many books moving the same direction between snapshots at once.

Flow per run:
  - Fetch odds across us+eu (includes Pinnacle), store raw to the odds history +
    Supabase odds_snapshots (line-movement data).
  - Compute sharp signals, store to Supabase `sharp_signals` (falls back to local
    CSV if that table isn't created yet), and print a report.

    python sharp_tracker.py                 # all of today's games
    python sharp_tracker.py --game ARI@SEA  # one game

Costs ~ (3 markets x 2 regions) credits per fetch on The Odds API.
"""

from __future__ import annotations

import argparse
import statistics
from datetime import datetime, timezone

import pandas as pd

import config
import market_data
from bet_evaluator import american_to_implied, load
from backtest import db
from backtest.import_snapshots import game_pk, TODAY

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
SHARP_CSV = config.EVAL_DATA_DIR / "sharp_signals.csv"


# ── Fetch (sharp + soft, us+eu) ───────────────────────────────────────────────
def fetch_sharp_odds() -> list[dict]:
    params = {"regions": config.ODDS_SHARP_REGIONS, "markets": config.ODDS_GAME_MARKETS,
              "oddsFormat": config.ODDS_FORMAT}
    data = market_data._get(f"/sports/{config.ODDS_SPORT_KEY}/odds", params)
    fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for ev in data:
        rows.extend(market_data._normalize_event(ev, fetched))
    return rows


# ── De-vig pairing ────────────────────────────────────────────────────────────
def _pair_key(market: str, side: str, line: str) -> str:
    if market == "ml":
        return "ml"
    if market == "runline":
        return "runline"
    if market == "total":
        return f"total@{line}"
    if market == "team_total":
        team = side.split("_")[0]
        return f"{team}@{line}"
    return f"{market}@{line}"


def devig_game(rows: list[dict]) -> dict:
    """
    rows: normalized odds for ONE game (book, market, side, line, odds).
    Returns {(market, pair_key, selection): {book: novig_prob}}.
    """
    # group by (book, market, pair_key) -> {selection: implied}
    groups: dict[tuple, dict] = {}
    for r in rows:
        try:
            odds = int(float(r["odds"]))
        except (ValueError, TypeError):
            continue
        pk = _pair_key(r["market"], r["side"], r["line"])
        key = (r["book"], r["market"], pk)
        groups.setdefault(key, {})[r["side"]] = american_to_implied(odds)

    out: dict[tuple, dict] = {}
    for (book, market, pk), sides in groups.items():
        if len(sides) != 2:           # need both sides to remove vig
            continue
        total = sum(sides.values())
        if total <= 0:
            continue
        for sel, imp in sides.items():
            out.setdefault((market, pk, sel), {})[book] = imp / total
    return out


# ── Sharp signal computation ──────────────────────────────────────────────────
def sharp_signals_for_game(gpk: int, rows: list[dict]) -> list[dict]:
    novig = devig_game(rows)
    # organize per (market, pair_key): list of (selection, sharp_med, soft_med, ...)
    pairs: dict[tuple, list] = {}
    for (market, pk, sel), bybook in novig.items():
        sharp = [p for b, p in bybook.items() if b in config.SHARP_BOOKS]
        soft = [p for b, p in bybook.items() if b not in config.SHARP_BOOKS]
        if not sharp or not soft:
            continue
        pairs.setdefault((market, pk), []).append({
            "selection": sel,
            "sharp": statistics.median(sharp), "soft": statistics.median(soft),
            "n_sharp": len(sharp), "n_soft": len(soft),
            "sharp_books": sorted(b for b in bybook if b in config.SHARP_BOOKS),
        })

    signals = []
    for (market, pk), sides in pairs.items():
        # the side sharps favor most relative to soft consensus
        best = max(sides, key=lambda s: s["sharp"] - s["soft"])
        div = best["sharp"] - best["soft"]
        if div < config.SHARP_DIVERGENCE_MIN:
            continue
        mv = market_data.line_movement(*_lookup_args(gpk, market, best["selection"]))
        signals.append({
            "game_pk": gpk, "snapshot_time": NOW, "market_type": market,
            "selection": best["selection"],
            "sharp_novig_prob": round(best["sharp"], 4),
            "soft_novig_prob": round(best["soft"], 4),
            "divergence": round(div, 4),
            "n_sharp_books": best["n_sharp"], "n_soft_books": best["n_soft"],
            "line_open": mv["open"] if mv else None,
            "line_current": mv["current"] if mv else None,
            "line_delta": mv["delta"] if mv else None,
            "steam_flag": bool(mv and mv["snapshots"] > 1 and abs(mv["delta"]) >= 10),
            "steam_books": None,
            "sharp_books_used": ",".join(best["sharp_books"]),
            "source": "the-odds-api",
        })
    return signals


def _lookup_args(gpk, market, selection):
    # market_data.line_movement(away, home, market, side, line) — resolve teams from gpk
    g = _GAME_BY_PK.get(gpk, ("", ""))
    return (g[0], g[1], market, selection, None)


_GAME_BY_PK: dict[int, tuple] = {}


def run(only_game: str | None = None):
    m = load("today_matchups.csv")
    if m is None:
        raise SystemExit("today_matchups.csv not found.")
    m["Away"] = m["Away"].astype(str).str.upper().str.strip()
    m["Home"] = m["Home"].astype(str).str.upper().str.strip()
    matchups = set(zip(m["Away"], m["Home"]))
    for a, h in matchups:
        _GAME_BY_PK[game_pk(TODAY, a, h)] = (a, h)

    print(f"Fetching sharp+soft odds ({config.ODDS_SHARP_REGIONS})...")
    raw = fetch_sharp_odds()
    if not raw:
        raise SystemExit("  No odds returned.")
    # store raw for movement history + Supabase odds_snapshots
    market_data.store(raw)
    try:
        from backtest import import_odds
        import_odds.run()
    except SystemExit as e:
        print(f"  (Supabase odds store skipped: {e})")

    # group raw rows by game
    by_game: dict[tuple, list] = {}
    for r in raw:
        by_game.setdefault((r["away"], r["home"]), []).append(r)

    sharp_books_seen = sorted({r["book"] for r in raw if r["book"] in config.SHARP_BOOKS})
    print(f"  Sharp books present: {sharp_books_seen or 'NONE (check region/credits)'}")

    all_signals = []
    for (away, home), rows in by_game.items():
        if (away, home) not in matchups:
            continue
        if only_game and f"{away}@{home}" != only_game.upper():
            continue
        all_signals.extend(sharp_signals_for_game(game_pk(TODAY, away, home), rows))

    _persist(all_signals)
    _report(all_signals)


def _persist(signals: list[dict]):
    # local CSV (always)
    config.EVAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if signals:
        df = pd.DataFrame(signals)
        header = not SHARP_CSV.exists()
        df.to_csv(SHARP_CSV, mode="a", header=header, index=False)
    # Supabase (if table exists)
    try:
        n = db.insert("sharp_signals", signals)
        print(f"  Stored {n} sharp signals in Supabase (sharp_signals).")
    except SystemExit as e:
        print(f"  sharp_signals not in Supabase yet ({e}). Saved to {SHARP_CSV.name}.")
        print("  -> re-run backtest/schema.sql in the SQL editor to enable Supabase storage.")


def _report(signals: list[dict]):
    if not signals:
        print("\n  No sharp lean above threshold across today's slate.\n")
        return
    print(f"\n  SHARP MONEY REPORT — {len(signals)} signal(s)")
    print(f"  {'GAME/MKT':<22} {'SHARP SIDE':<16} {'DIV':>6} {'SHARP%':>7} {'SOFT%':>7} MOVE")
    for s in sorted(signals, key=lambda x: -x["divergence"]):
        g = _GAME_BY_PK.get(s["game_pk"], ("?", "?"))
        mv = ""
        if s["line_open"] is not None:
            mv = f"{s['line_open']:+d}->{s['line_current']:+d}"
            if s["steam_flag"]:
                mv += " STEAM"
        label = f"{g[0]}@{g[1]} {s['market_type']}"
        print(f"  {label:<22} {s['selection']:<16} {s['divergence']*100:>5.1f} "
              f"{s['sharp_novig_prob']*100:>6.1f} {s['soft_novig_prob']*100:>6.1f} {mv}")
    print()


def main():
    p = argparse.ArgumentParser(description="Line movement + sharp money tracker.")
    p.add_argument("--game", help='Limit to "AWAY@HOME"')
    run(p.parse_args().game)


if __name__ == "__main__":
    main()
