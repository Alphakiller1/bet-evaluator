"""
Phase A — settle model_predictions against final outcomes + compute CLV.

For every unsettled prediction whose game has an outcome: grade whether the
predicted side won/pushed, and compute a CLV proxy from the closing line
(closing market-implied prob for the side minus the implied prob at prediction
time; positive = the market moved toward our side = we beat the close).

    python -m backtest.import_outcomes     # first, so outcomes exist
    python -m backtest.settle_predictions
"""

from __future__ import annotations

import config


def _grade(mt, sel, line, home, away, hr, ar, total, margin, winner):
    """Return (won, push) for one prediction given the outcome, or (None, None)."""
    if mt == "ml":
        return (winner == sel, False)
    if mt == "total":
        if total is None or line is None:
            return (None, None)
        if total == line:
            return (None, True)
        return ((total > line) if sel == "over" else (total < line), False)
    if mt == "team_total":
        if line is None:
            return (None, None)
        team, _, ou = sel.partition("_")
        tr = hr if team == home else ar
        if tr is None:
            return (None, None)
        if tr == line:
            return (None, True)
        return ((tr > line) if ou == "over" else (tr < line), False)
    if mt == "runline":
        if margin is None or line is None:
            return (None, None)
        team_margin = margin if sel == home else -margin   # margin_home = home - away
        covered = team_margin + line                       # line is signed (-1.5 / +1.5)
        if covered == 0:
            return (None, True)
        return (covered > 0, False)
    return (None, None)


def run():
    import psycopg2
    conn = psycopg2.connect(config.SUPABASE_DB_URL, connect_timeout=20)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("select game_pk, home_team, away_team from games")
    games = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.execute("select game_pk, home_runs, away_runs, total_runs, margin_home, winner_team "
                "from game_outcomes")
    outs = {r[0]: r[1:] for r in cur.fetchall()}
    cur.execute("select game_pk, market_type, selection, closing_implied_probability "
                "from v_closing_lines")
    closing = {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}

    cur.execute("select prediction_id, game_pk, market_type, selection, line, "
                "market_implied_probability from model_predictions where settled = false")
    preds = cur.fetchall()

    updates = []
    for pid, gpk, mt, sel, line, implied in preds:
        if gpk not in outs or gpk not in games:
            continue
        home, away = games[gpk]
        hr, ar, total, margin, winner = outs[gpk]
        won, push = _grade(mt, sel, float(line) if line is not None else None,
                           home, away, hr, ar, total, margin, winner)
        if won is None and not push:
            continue
        cimp = closing.get((gpk, mt, sel))
        clv = (float(cimp) - float(implied)) if cimp is not None and implied is not None else None
        updates.append((None if won is None else bool(won), bool(push), cimp, clv, pid))

    if updates:
        cur.executemany(
            "update model_predictions set settled=true, won=%s, push=%s, "
            "closing_implied_probability=%s, clv=%s, settled_at=now() "
            "where prediction_id=%s", updates)
    conn.close()
    print(f"  Settled {len(updates)} predictions (of {len(preds)} unsettled; matched to outcomes).")


if __name__ == "__main__":
    run()
