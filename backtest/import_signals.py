"""
Ingest today's MLBMA cross-metric signals into Supabase (pre-game snapshot).

The Signal Board page was retired; signals now live in the warehouse so every
tool can learn from them. This reads the pipeline's signal CSVs and upserts:
  - mlbma_signals       (10 signals x 2 lineup sides per game, with verdicts)
  - mlbma_convergence   (weighted convergence + is_convergence_play per side)
both stamped snapshot_time = now (UTC) and metric_version. Nothing here uses
post-game info; after games settle, v_signal_outcomes / v_signal_performance
grade which signals actually predict.

    python -m backtest.import_signals
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import config
from bet_evaluator import load, _num
from backtest import db
from backtest.import_snapshots import game_pk, scheduled_start

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
TODAY = date.today().isoformat()


def _bool(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def run():
    sig = load("signals_today.csv")
    conv = load("signals_convergence.csv")
    if sig is None and conv is None:
        raise SystemExit("No signal CSVs found — run core.compute_signals first.")

    matchups = load("today_matchups.csv")
    sched = {}
    if matchups is not None:
        for _, g in matchups.iterrows():
            a = str(g.get("Away", "")).upper().strip()
            h = str(g.get("Home", "")).upper().strip()
            if a and h:
                sched[(a, h)] = scheduled_start(TODAY, g.get("Time"))

    games, signals, convergence = {}, [], []

    def _game(a: str, h: str) -> int:
        gpk = game_pk(TODAY, a, h)
        games.setdefault(gpk, {
            "game_pk": gpk, "season": getattr(config, "CURRENT_SEASON", 2026),
            "game_date": TODAY, "scheduled_start": sched.get((a, h)),
            "home_team": h, "away_team": a, "status": "scheduled", "updated_at": NOW,
        })
        return gpk

    if sig is not None:
        for _, r in sig.iterrows():
            a = str(r.get("away", "")).upper().strip()
            h = str(r.get("home", "")).upper().strip()
            if not a or not h:
                continue
            gpk = _game(a, h)
            signals.append({
                "game_pk": gpk, "snapshot_time": NOW, "game_date": TODAY,
                "away": a, "home": h, "side": str(r.get("side", "")).lower(),
                "signal_name": r.get("signal_name"), "fired": _bool(r.get("fired")),
                "direction": r.get("direction"), "magnitude": _num(r.get("magnitude")),
                "bet_angle": r.get("bet_angle"), "verdict_text": r.get("verdict_text"),
                "metric_version": config.METRIC_VERSION,
            })

    if conv is not None:
        for _, r in conv.iterrows():
            a = str(r.get("away", "")).upper().strip()
            h = str(r.get("home", "")).upper().strip()
            if not a or not h:
                continue
            gpk = _game(a, h)
            convergence.append({
                "game_pk": gpk, "snapshot_time": NOW, "game_date": TODAY,
                "away": a, "home": h, "side": str(r.get("side", "")).lower(),
                "convergence_count": _num(r.get("convergence_count")),
                "convergence_direction": r.get("convergence_direction"),
                "is_convergence_play": _bool(r.get("is_convergence_play")),
                "signals_fired": int(_num(r.get("signals_fired")) or 0),
                "metric_version": config.METRIC_VERSION,
            })

    if games:
        db.upsert("games", list(games.values()), "game_pk")
    if signals:
        db.upsert("mlbma_signals", signals, "game_pk,snapshot_time,side,signal_name")
    if convergence:
        db.upsert("mlbma_convergence", convergence, "game_pk,snapshot_time,side")

    fired = sum(1 for s in signals if s["fired"])
    plays = sum(1 for c in convergence if c["is_convergence_play"])
    print(f"  games={len(games)}  signals={len(signals)} ({fired} fired)  "
          f"convergence={len(convergence)} ({plays} plays)")
    print(f"  snapshot_time={NOW}  metric_version={config.METRIC_VERSION}")


if __name__ == "__main__":
    run()
