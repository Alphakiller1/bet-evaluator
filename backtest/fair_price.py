"""
Phase B — calibrated fair-price lookup (read-only).

Given a market + the model's raw edge, return the EMPIRICAL win rate / ROI for
that (market, edge-bucket) from settled history, when the sample is large enough.
The daily report compares this against the raw model probability to flag
contradictions. It does NOT auto-adjust the evaluator — the user approves changes.
"""

from __future__ import annotations

import math

from backtest import db

EDGE_BUCKET = 0.025


def _bucket(edge: float) -> float:
    return round(math.floor(edge / EDGE_BUCKET) * EDGE_BUCKET, 4)


def calibrated(market_type: str, edge: float, min_n: int = 20) -> dict | None:
    """Empirical win_rate / roi / overconfidence for this market+edge bucket,
    or None if the settled sample is below min_n (honest-empty)."""
    b = _bucket(edge)
    rows = db.select("v_edge_sustainability",
                     f"?market_type=eq.{market_type}&edge_bucket=eq.{b}")
    if not rows or (rows[0].get("n") or 0) < min_n:
        return None
    r = rows[0]
    return {
        "market_type": market_type, "edge_bucket": b, "n": r["n"],
        "empirical_win_rate": r["win_rate"], "avg_model_prob": r["avg_model_prob"],
        "overconfidence": r["overconfidence"], "roi_per_unit": r["roi_per_unit"],
        "avg_clv": r["avg_clv"],
    }


def all_buckets(min_n: int = 20) -> list[dict]:
    """All sustainability rows meeting the sample threshold (drives the report)."""
    rows = db.select("v_edge_sustainability", "?order=market_type.asc,edge_bucket.asc")
    return [r for r in rows if (r.get("n") or 0) >= min_n]
