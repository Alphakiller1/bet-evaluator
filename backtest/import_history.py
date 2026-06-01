"""
Ingest ALL past games + outcomes (game_results.csv) into Supabase as historical
truth, kept separate from the upcoming slate.

  - Past games  -> games.status = 'final'   (have outcomes)
  - Future games -> games.status = 'scheduled' (today's slate, from import_snapshots)

Past ODDS are NOT ingested here: The Odds API historical endpoint is paid-only
(confirmed 401 on the free plan). Past analysis is therefore outcome-based; the
sharp track record stays forward-only until a paid historical-odds source exists.

    python -m backtest.import_history
"""

from __future__ import annotations

from bet_evaluator import load, _num
from backtest import db
from backtest.import_snapshots import game_pk, ABBRS

VALID = set(ABBRS)


def _b(v):
    return str(v).strip().lower() in ("true", "1", "w")


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def run():
    g = load("game_results.csv")
    if g is None:
        raise SystemExit("game_results.csv not found.")
    g["home_away"] = g["home_away"].astype(str).str.strip().str.lower()
    home = g[g["home_away"] == "home"].copy()
    away_rows = {r["game_pk"]: r for _, r in g[g["home_away"] == "away"].iterrows()}

    games, outcomes = [], []
    seen = set()
    for _, h in home.iterrows():
        ht, at = str(h["team"]).upper(), str(h["opp"]).upper()
        if ht not in VALID or at not in VALID:
            continue
        date = str(h["date"])[:10]
        spk = game_pk(date, at, ht)
        if spk in seen:
            continue
        seen.add(spk)
        hr, ar = _num(h["team_runs"]), _num(h["opp_runs"])
        if hr is None or ar is None:
            continue
        games.append({
            "game_pk": spk, "season": 2026, "game_date": date,
            "scheduled_start": f"{date}T17:00:00+00:00",   # nominal; outcomes are what matter
            "home_team": ht, "away_team": at, "status": "final",
        })
        a = away_rows.get(h["game_pk"])
        outcomes.append({
            "game_pk": spk, "home_runs": int(hr), "away_runs": int(ar),
            "total_runs": int(hr + ar), "margin_home": int(hr - ar),
            "winner_team": ht if str(h["result"]).strip().upper() == "W" else at,
            "home_starter_id": _int(h.get("team_starter_id")),
            "away_starter_id": _int(h.get("opp_starter_id")),
            "home_quality_start": _b(h.get("team_quality_start")),
            "away_quality_start": _b(h.get("opp_quality_start")),
            "blown_save_home": _b(h.get("blown_save")),
            "blown_save_away": _b(a.get("blown_save")) if a is not None else None,
        })

    print(f"  Upserting {len(games)} historical games + outcomes...")
    db.upsert("games", games, "game_pk")
    db.upsert("game_outcomes", outcomes, "game_pk")
    print(f"  games total: {db.count('games')} "
          f"(past/final via v_games_past: {db.count('v_games_past')}, "
          f"upcoming: {db.count('v_games_upcoming')})")
    print(f"  game_outcomes total: {db.count('game_outcomes')}")


if __name__ == "__main__":
    run()
