"""
Phase 4a — ingest final game outcomes into Supabase game_outcomes.

Reads the pipeline's game_results.csv (two rows per game; home + away
perspective), collapses to one outcome row per game keyed by the SAME surrogate
game_pk used for snapshots (crc32 of date|away|home), and upserts only games that
already exist in our games table.

    python -m backtest.import_outcomes
"""

from __future__ import annotations

from bet_evaluator import load, _num
from backtest import db
from backtest.import_snapshots import game_pk


def _b(v):
    return str(v).strip().lower() in ("true", "1", "w")


def run():
    g = load("game_results.csv")
    if g is None:
        raise SystemExit("game_results.csv not found.")
    g["home_away"] = g["home_away"].astype(str).str.strip().str.lower()

    # index away-perspective rows by real game_pk for blown_save_away / starters
    away_rows = {r["game_pk"]: r for _, r in g[g["home_away"] == "away"].iterrows()}

    # which surrogate game_pks exist in our games table (FK)
    existing = {int(r["game_pk"]) for r in db.select("games", "?select=game_pk")}

    outcomes = []
    for _, h in g[g["home_away"] == "home"].iterrows():
        date = str(h["date"])[:10]
        home, away = str(h["team"]).upper(), str(h["opp"]).upper()
        spk = game_pk(date, away, home)
        if spk not in existing:
            continue
        a = away_rows.get(h["game_pk"])
        hr, ar = _num(h["team_runs"]), _num(h["opp_runs"])
        outcomes.append({
            "game_pk": spk,
            "home_runs": hr, "away_runs": ar,
            "total_runs": (hr + ar) if hr is not None and ar is not None else None,
            "margin_home": (hr - ar) if hr is not None and ar is not None else None,
            "winner_team": home if str(h["result"]).strip().upper() == "W" else away,
            "home_starter_id": _num(h.get("team_starter_id")),
            "away_starter_id": _num(h.get("opp_starter_id")),
            "home_starter_ip": _num(h.get("team_starter_ip")),
            "away_starter_ip": _num(h.get("opp_starter_ip")),
            "home_starter_er": _num(h.get("team_starter_er")),
            "away_starter_er": _num(h.get("opp_starter_er")),
            "home_quality_start": _b(h.get("team_quality_start")),
            "away_quality_start": _b(h.get("opp_quality_start")),
            "save_pitcher_id": _num(h.get("save_pitcher_id")),
            "blown_save_home": _b(h.get("blown_save")),
            "blown_save_away": _b(a.get("blown_save")) if a is not None else None,
        })

    n = db.upsert("game_outcomes", outcomes, "game_pk")
    print(f"  Upserted {n} game outcomes (matched to our games).")
    print(f"  game_outcomes total: {db.count('game_outcomes')}")


if __name__ == "__main__":
    run()
