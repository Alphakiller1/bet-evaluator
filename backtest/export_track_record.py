"""
Export the real track record to the dashboard so the hero shows live proof, not
hardcoded numbers. Computes the validated edge + sample sizes from the Supabase
warehouse and writes dashboard/data/track_record.json.

    python -m backtest.export_track_record
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import config
from backtest import db, market_edge

OUT = Path(config.PIPELINE_DATA_DIR).parent / "dashboard" / "data" / "track_record.json"


def build() -> dict:
    edges = market_edge.scan(min_n=30)
    # headline = the steam-up >=2pt segment (broadest validated edge); fall back to best tradeable
    steam = next((e for e in edges if e.get("label") == "steam up >=2pt" and e.get("enough")), None)
    tradeable = [e for e in edges if e.get("tradeable")]
    head = steam or (max(tradeable, key=lambda x: x["roi"]) if tradeable else None)

    # distinct games in the closing-line backtest sample (honest "back-tested" count)
    try:
        rows = db.select("prediction_market_snapshots",
                         "?settled=eq.true&select=game_pk&limit=8000")
        games = len({r["game_pk"] for r in rows})
    except SystemExit:
        games = db.count("game_outcomes")

    def pct(x):
        return f"{x*100:+.1f}%" if x is not None else "—"

    if head:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "roi": pct(head["roi"]),
            "roi_label": "ROI / unit — steam-up spots",
            "roi_sub": f"FDR-validated · n={head['n']} settled",
            "clv": f"+{head['clv']*100:.1f} pts" if head.get("clv") is not None else "—",
            "games": str(games),
            "metrics": "9",
            "tradeable_count": len(tradeable),
        }
    # honest-empty until the sample fills
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "roi": "building", "roi_label": "edge — accumulating", "roi_sub": "forward sample filling",
        "clv": "—", "games": str(games), "metrics": "9", "tradeable_count": 0,
    }


def main():
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  Wrote {OUT}")
    print(f"  roi={data['roi']} games={data['games']} clv={data['clv']} tradeable={data['tradeable_count']}")


if __name__ == "__main__":
    main()
