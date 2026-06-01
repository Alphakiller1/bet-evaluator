"""
Phase 2 — capture today's PRE-GAME metric snapshots into Supabase.

Run this before first pitch. It records, with snapshot_time = now (UTC):
  - games           (deterministic surrogate game_pk from date+teams, scheduled_start from slate time)
  - team_metric_snapshots   (offense stack vs opposing-SP hand)
  - pitcher_metric_snapshots (both starters)
  - bullpen_metric_snapshots (both teams)
all tagged with the current metric_version. This is how the warehouse banks truth
going forward; nothing here uses post-game information.

    python -m backtest.import_snapshots
"""

from __future__ import annotations

import zlib
from datetime import date, datetime, timezone

import config
from bet_evaluator import load, _num, _pct
from backtest import db

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
RUN_ID = "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
TODAY = date.today().isoformat()
ABBRS = sorted(config.PARK_FACTORS.keys())
ABBR_TO_NAME = {v: k for k, v in config.TEAM_NAME_TO_ABBR.items()}


def game_pk(d: str, away: str, home: str) -> int:
    """Deterministic surrogate key from date+teams (stable across snapshot/outcome)."""
    return zlib.crc32(f"{d}|{away}|{home}".encode())


def scheduled_start(d: str, time_str: str) -> str | None:
    """Slate time like '12:15 PM ET' -> ISO timestamp (ET = -04:00 in season)."""
    t = str(time_str).replace("ET", "").strip()
    try:
        hm = datetime.strptime(t, "%I:%M %p")
        return f"{d}T{hm.hour:02d}:{hm.minute:02d}:00-04:00"
    except ValueError:
        return None


def offense_row(df, team):
    if df is None:
        return {}
    m = df[df["Tm"].astype(str).str.upper() == team]
    return m.iloc[0].to_dict() if not m.empty else {}


def seed_reference():
    teams = [{"team_id": i + 1, "team_abbr": a, "team_name": ABBR_TO_NAME.get(a, a)}
             for i, a in enumerate(ABBRS)]
    db.upsert("teams", teams, "team_id")
    db.upsert("metric_versions", [{
        "metric_version": config.METRIC_VERSION,
        "osi_formula_json": {"rcv": 0.43, "abq": 0.37, "obr": 0.20},
        "pitch_score_formula_json": {"k_pct": 0.40, "inv_bb_pct": 0.35, "inv_hr9": 0.25},
        "notes": "Weights per mlbma_pipeline ecosystem; snapshots tag this version.",
    }], "metric_version")
    print(f"  Seeded {len(teams)} teams + metric_version {config.METRIC_VERSION}.")


def run():
    m = load("today_matchups.csv")
    if m is None:
        raise SystemExit("today_matchups.csv not found — run the pipeline first.")
    m["Away"] = m["Away"].astype(str).str.upper().str.strip()
    m["Home"] = m["Home"].astype(str).str.upper().str.strip()

    rhp, lhp = load("metrics_vs_RHP.csv"), load("metrics_vs_LHP.csv")
    pals, oor = load("metrics_pals.csv"), load("metrics_oor.csv")
    tp = load("team_profiles.csv")
    if tp is not None:
        tp["team"] = tp["team"].astype(str).str.upper().str.strip()

    seed_reference()
    games, team_snaps, pit_snaps, pen_snaps = [], [], [], []

    for _, g in m.iterrows():
        away, home = g["Away"], g["Home"]
        gpk = game_pk(TODAY, away, home)
        away_hand = str(g.get("Away_Hand", "R")).upper()[:1]
        home_hand = str(g.get("Home_Hand", "R")).upper()[:1]

        games.append({
            "game_pk": gpk, "season": config.__dict__.get("CURRENT_SEASON", 2026),
            "game_date": TODAY, "scheduled_start": scheduled_start(TODAY, g.get("Time")),
            "home_team": home, "away_team": away, "status": "scheduled", "updated_at": NOW,
        })

        # Team offense snapshots (lineup vs opposing-SP hand).
        for team, opp, is_home, opp_hand in ((away, home, False, home_hand), (home, away, True, away_hand)):
            split_df = rhp if opp_hand == "R" else lhp
            row = offense_row(split_df, team)
            if not row:
                continue
            abq, rcv, obr = _num(row.get("ABQ")), _num(row.get("RCV")), _num(row.get("OBR"))
            team_snaps.append({
                "game_pk": gpk, "team": team, "opponent": opp, "snapshot_time": NOW,
                "lineup_status": "projected", "split_used": f"vs_{opp_hand}HP",
                "window_used": "YTD", "is_home": is_home, "opposing_starter_hand": opp_hand,
                "abq": abq, "rcv": rcv, "obr": obr, "osi": _num(row.get("OSI")),
                "proj_osi": _num(row.get("projOSI")), "reg_signal": _num(row.get("reg_signal")),
                "pals": _num(offense_row(pals, team).get("PALS")),
                "oor": _num(offense_row(oor, team).get("OOR")),
                "pp_gap": (abq - rcv) if abq is not None and rcv is not None else None,
                "df_gap": (rcv - obr) if rcv is not None and obr is not None else None,
                "wrc_plus": _num(row.get("wRC+")), "woba": _num(row.get("wOBA")),
                "xwoba": _num(row.get("xwOBA")), "slg": _num(row.get("SLG")),
                "k_pct": _num(row.get("K%")), "bb_pct": _num(row.get("BB%")),
                "barrel_pct": _num(row.get("Barrel%")), "hardhit_pct": _num(row.get("HardHit%")),
                "metric_version": config.METRIC_VERSION, "pipeline_run_id": RUN_ID,
            })

        # Starter snapshots.
        for team, pre in ((away, "Away"), (home, "Home")):
            pit_snaps.append({
                "game_pk": gpk, "pitcher_name": str(g.get(f"{pre}_SP", "TBD")), "team": team,
                "snapshot_time": NOW, "hand": str(g.get(f"{pre}_Hand", "R")).upper()[:1],
                "role": "starter", "fip": _num(g.get(f"{pre}_FIP")),
                "k_pct": _pct(g.get(f"{pre}_K%")), "bb_pct": _pct(g.get(f"{pre}_BB%")),
                "hr9": _num(g.get(f"{pre}_HR9")),
                "metric_version": config.METRIC_VERSION, "pipeline_run_id": RUN_ID,
            })

        # Bullpen snapshots.
        for team in (away, home):
            if tp is None:
                continue
            r = tp[tp["team"] == team]
            if r.empty:
                continue
            row = r.iloc[0]
            pen_snaps.append({
                "game_pk": gpk, "team": team, "snapshot_time": NOW,
                "overall_era": _num(row.get("bullpen_era")),
                "osi_allowed": _num(row.get("bullpen_osi_allowed")),
                "high_leverage_era": _num(row.get("bullpen_high_lev_era")),
                "inherited_scored_pct": _num(row.get("bullpen_ir_scored_pct")),
                "metric_version": config.METRIC_VERSION, "pipeline_run_id": RUN_ID,
            })

    db.upsert("games", games, "game_pk")
    db.insert("team_metric_snapshots", team_snaps)
    db.insert("pitcher_metric_snapshots", pit_snaps)
    db.insert("bullpen_metric_snapshots", pen_snaps)
    print(f"  games={len(games)}  team_snaps={len(team_snaps)}  "
          f"pitcher_snaps={len(pit_snaps)}  bullpen_snaps={len(pen_snaps)}")
    print(f"  snapshot_time={NOW}  metric_version={config.METRIC_VERSION}")


if __name__ == "__main__":
    run()
