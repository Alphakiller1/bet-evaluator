"""
Phase A — log each bet evaluation into Supabase `model_predictions`.

Called from bet_evaluator after build_analysis(). Every evaluation becomes a
timestamped, look-ahead-safe prediction row (model prob, market-implied prob,
edge, EV, fair odds, projected runs, features) stamped with MODEL_VERSION /
METRIC_VERSION. This is what lets the warehouse grade and calibrate the model
over time. Settlement + CLV happen post-game via backtest.settle_predictions.

Not a CLI; bet_evaluator imports log() and calls it (guarded — a Supabase/
network failure must never block the evaluation itself).
"""

from __future__ import annotations

from datetime import datetime, timezone

import config
from bet_evaluator import load, resolve_side_key
from backtest import db
from backtest.import_snapshots import game_pk, scheduled_start, TODAY


def _side_role(market: str, selection: str, implied: float | None, home: str) -> str:
    """fav/dog (ml,runline) or over/under (total,team_total) — for segmenting later."""
    m = market.lower()
    if m in ("total", "team_total"):
        return "over" if selection.endswith("over") or selection == "over" else "under"
    if implied is None:
        return "na"
    return "fav" if implied >= 0.5 else "dog"


def _seed_versions() -> None:
    """Ensure the FK targets exist (idempotent). model_predictions references both
    model_versions and metric_versions."""
    db.upsert("model_versions", [{
        "model_version": config.MODEL_VERSION,
        "model_type": "expected-runs heuristic",
        "calibration_method": "uncalibrated (forward-accumulating)",
        "notes": "bet_evaluator transparent prior; calibrated via the warehouse over time.",
    }], "model_version")
    db.upsert("metric_versions", [{
        "metric_version": config.METRIC_VERSION,
        "notes": "Pipeline metric snapshot version stamped onto predictions.",
    }], "metric_version")


def _ensure_game(away: str, home: str, gpk: int) -> None:
    """Upsert the game row so model_predictions' FK holds (idempotent)."""
    sched = None
    m = load("today_matchups.csv")
    if m is not None and "Away" in m.columns:
        m["Away"] = m["Away"].astype(str).str.upper().str.strip()
        m["Home"] = m["Home"].astype(str).str.upper().str.strip()
        row = m[(m["Away"] == away) & (m["Home"] == home)]
        if not row.empty:
            sched = scheduled_start(TODAY, row.iloc[0].get("Time"))
    db.upsert("games", [{
        "game_pk": gpk, "season": getattr(config, "CURRENT_SEASON", 2026),
        "game_date": TODAY, "scheduled_start": sched,
        "home_team": home, "away_team": away, "status": "scheduled",
    }], "game_pk")


def log(a: dict) -> int | None:
    """Insert one model_predictions row from a bet_evaluator analysis dict.
    Returns the game_pk, or None if logging failed (never raises to the caller)."""
    try:
        gd = a["game"]
        away, home = gd.away, gd.home
        gpk = game_pk(TODAY, away, home)
        _seed_versions()
        _ensure_game(away, home, gpk)

        v = a["value"]
        probs = a["probs"]
        selection = resolve_side_key(a["market"], a["side"], gd, a.get("ou"))
        implied = v.get("implied")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        features = {
            "side": a["side"], "ou": a.get("ou"),
            "away_osi": gd.away_osi, "home_osi": gd.home_osi,
            "away_fip": gd.away_fip, "home_fip": gd.home_fip,
            "away_hr9": gd.away_hr9, "home_hr9": gd.home_hr9,
            "away_k": gd.away_k, "home_k": gd.home_k,
            "away_hand": gd.away_hand, "home_hand": gd.home_hand,
            "park_factor": gd.park_factor,
            "kelly_quarter": v.get("kelly_quarter"),
            "tier": v.get("tier"), "units": v.get("units"),
            "signals_fired": sum(len(s) for s in (a.get("signals") or {}).values()),
            "odds_evaluated": a.get("odds"),
        }

        row = {
            "game_pk": gpk, "prediction_time": now,
            "market_type": a["market"], "selection": selection, "line": a.get("line"),
            "model_version": config.MODEL_VERSION, "metric_version": config.METRIC_VERSION,
            "model_probability": a["model_p"],
            "market_implied_probability": implied,
            "edge": v.get("edge"), "expected_value": v.get("ev_per_unit"),
            "fair_odds": v.get("fair_odds"),
            "projected_home_runs": getattr(probs, "exp_home_runs", None),
            "projected_away_runs": getattr(probs, "exp_away_runs", None),
            "projected_total": getattr(probs, "exp_total", None),
            "projected_margin": getattr(probs, "exp_margin", None),
            "side_role": _side_role(a["market"], selection, implied, home),
            "features_json": features,
            "verdict": str(v.get("verdict", "")).lower(),
        }
        db.insert("model_predictions", [row])
        return gpk
    except (Exception, SystemExit) as e:  # never block an evaluation on a warehouse hiccup
        print(f"  (prediction not logged to Supabase: {e})")
        return None
