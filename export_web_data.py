"""
Export the data the static web UI needs into docs/data/site.json.

Reads the same sources as the evaluator (pipeline matchups/signals + scraped odds),
plus the model constants, so the in-browser JS produces identical numbers to the
Python tool. Run after a pipeline refresh / odds fetch:

    python export_web_data.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

import config
from bet_evaluator import (
    load_game, load_signals_for_game, load_convergence_for_game, refresh_anchors, load,
)

DOCS_DATA = Path(__file__).resolve().parent / "docs" / "data"


_TEAM_PROFILES = None


def team_risk(team: str) -> dict:
    """Bullpen ERA + offense window trend for the risk layer."""
    global _TEAM_PROFILES
    if _TEAM_PROFILES is None:
        tp = load("team_profiles.csv")
        if tp is not None and "team" in tp.columns:
            tp["team"] = tp["team"].astype(str).str.upper().str.strip()
            _TEAM_PROFILES = tp
        else:
            _TEAM_PROFILES = pd.DataFrame()
    if _TEAM_PROFILES.empty:
        return {}
    r = _TEAM_PROFILES[_TEAM_PROFILES["team"] == team]
    if r.empty:
        return {}
    row = r.iloc[0]
    bp = row.get("bullpen_era")
    try:
        bp = float(bp)
    except (TypeError, ValueError):
        bp = None
    return {"bullpen_era": bp, "window_direction": str(row.get("window_direction", "")).strip()}


def game_odds(away: str, home: str) -> list[dict]:
    """All scraped odds rows for one game (the JS picks best price per side)."""
    if not config.ODDS_LATEST_CSV.exists():
        return []
    df = pd.read_csv(config.ODDS_LATEST_CSV, dtype=str).fillna("")
    g = df[(df["away"] == away) & (df["home"] == home)]
    return [
        {"book": r["book"], "market": r["market"], "side": r["side"],
         "line": r["line"], "odds": r["odds"]}
        for _, r in g.iterrows()
    ]


def build() -> dict:
    matchups = load("today_matchups.csv")
    if matchups is None:
        raise SystemExit("today_matchups.csv not found — run the pipeline first.")
    matchups["Away"] = matchups["Away"].astype(str).str.upper().str.strip()
    matchups["Home"] = matchups["Home"].astype(str).str.upper().str.strip()
    anchors = refresh_anchors()

    games = []
    for _, m in matchups.iterrows():
        away, home = m["Away"], m["Home"]
        try:
            gd = load_game(away, home)
        except Exception as exc:
            print(f"  skip {away}@{home}: {exc}")
            continue
        sig = load_signals_for_game(away, home)
        conv = load_convergence_for_game(away, home)
        games.append({
            "away": gd.away, "home": gd.home,
            "away_sp": gd.away_sp, "home_sp": gd.home_sp,
            "away_hand": gd.away_hand, "home_hand": gd.home_hand,
            "away_osi": gd.away_osi, "home_osi": gd.home_osi,
            "away_fip": gd.away_fip, "home_fip": gd.home_fip,
            "away_hr9": gd.away_hr9, "home_hr9": gd.home_hr9,
            "away_k": gd.away_k, "home_k": gd.home_k,
            "park_factor": gd.park_factor, "weather": gd.weather,
            "away_risk": team_risk(away), "home_risk": team_risk(home),
            "signals": sig, "convergence": conv,
            "odds": game_odds(away, home),
        })

    return {
        "generated": date.today().isoformat(),
        "anchors": anchors,
        "model": {
            "LEAGUE_FIP": config.LEAGUE_FIP,
            "OSI_RUN_SENSITIVITY": config.OSI_RUN_SENSITIVITY,
            "SP_FIP_WEIGHT": config.SP_FIP_WEIGHT,
            "OFF_FACTOR_CLIP": list(config.OFF_FACTOR_CLIP),
            "PITCH_FACTOR_CLIP": list(config.PITCH_FACTOR_CLIP),
            "REGRESSION_TO_MEAN": config.REGRESSION_TO_MEAN,
            "HFA_RUNS": config.HFA_RUNS,
            "IMPLAUSIBLE_EDGE": config.IMPLAUSIBLE_EDGE,
            "CONFIDENCE_TIERS": config.CONFIDENCE_TIERS,
        },
        "games": games,
    }


def main() -> None:
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    data = build()
    out = DOCS_DATA / "site.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  Wrote {out} ({len(data['games'])} games).")


if __name__ == "__main__":
    main()
