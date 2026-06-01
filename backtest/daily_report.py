"""
Phase B-Report — daily report + contradiction monitor.

Surfaces where the layers DISAGREE and proposes congruence updates (recommend-only):
  - model raw confidence vs calibrated history (per market x edge bucket)
  - edges that are unprofitable despite a PLAY-grade model edge
  - markets where CLV is being lost (entries late / mispriced)
  - (later) sharp side vs model side; metric whose trend stopped predicting

Writes the report to:
  - Supabase `daily_reports` (the chase-discord-bot reads + posts it)
  - the vault `15-Reports/<date>.md` note

Honest-empty: gated by --min-n; until settled predictions accrue it says so.

    python -m backtest.daily_report [--min-n 20] [--date 2026-06-01]
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import config
from backtest import db

# Tolerances for flagging a contradiction.
OVERCONF_TOL = 0.03     # |predicted - actual| >= 3 pts = miscalibrated
CLV_LOSS_TOL = 0.50     # CLV beat-rate below 50% = losing the close


def _bucket_label(edge_bucket: float) -> str:
    lo = edge_bucket * 100
    return f"{lo:+.1f}..{lo + 2.5:+.1f}pt"


def build(min_n: int) -> dict:
    """Assemble the report dict from the settled-data views."""
    logged = db.count("model_predictions")
    sustain = [r for r in db.select("v_edge_sustainability",
                                    "?order=market_type.asc,edge_bucket.asc")
               if (r.get("n") or 0) >= min_n]
    clv = db.select("v_clv_beat_rate")

    contradictions: list[dict] = []

    for r in sustain:
        oc = r.get("overconfidence")
        if oc is not None and abs(oc) >= OVERCONF_TOL:
            direction = "over" if oc > 0 else "under"
            contradictions.append({
                "kind": "calibration",
                "market": r["market_type"],
                "detail": (f"{r['market_type']} edges {_bucket_label(r['edge_bucket'])}: "
                           f"model {direction}-confident — predicted "
                           f"{(r['avg_model_prob'] or 0)*100:.1f}% vs actual "
                           f"{(r['win_rate'] or 0)*100:.1f}% over {r['n']}"),
                "suggestion": (f"shade {r['market_type']} model {direction} by ~"
                               f"{abs(oc)*100:.1f} pts in this band"),
            })
        roi = r.get("roi_per_unit")
        if roi is not None and roi < 0 and (r.get("edge_bucket") or 0) >= 0.02:
            contradictions.append({
                "kind": "profitability",
                "market": r["market_type"],
                "detail": (f"{r['market_type']} {_bucket_label(r['edge_bucket'])} shows a "
                           f"model edge but is unprofitable (ROI {roi:+.3f}/u over {r['n']})"),
                "suggestion": f"raise the PLAY threshold for {r['market_type']} or recheck inputs",
            })

    for c in clv:
        n = c.get("n") or 0
        rate = c.get("clv_beat_rate")
        if n >= min_n and rate is not None and rate < CLV_LOSS_TOL:
            contradictions.append({
                "kind": "clv",
                "market": c["market_type"],
                "detail": (f"losing the close in {c['market_type']}: CLV beat-rate "
                           f"{rate*100:.0f}% over {n} (avg CLV {(c.get('avg_clv') or 0)*100:+.1f})"),
                "suggestion": f"enter {c['market_type']} earlier or tighten price shopping",
            })

    flags = []
    for r in sustain:
        if (r.get("roi_per_unit") or 0) > 0 and (r.get("avg_clv") or 0) > 0:
            flags.append(f"SUSTAINABLE: {r['market_type']} {_bucket_label(r['edge_bucket'])} "
                         f"(ROI {r['roi_per_unit']:+.3f}/u, +CLV, n={r['n']})")

    return {
        "logged": logged, "settled_buckets": len(sustain),
        "contradictions": contradictions, "flags": flags,
    }


def render_md(rep: dict, d: str, min_n: int) -> str:
    lines = [f"---", f"title: Daily Report {d}", f"tags: [report, betting, contradiction-monitor]",
             f"date: {d}", f"---", "", f"# Daily Report — {d}", ""]
    if not rep["settled_buckets"]:
        lines += [
            f"> **Insufficient sample.** {rep['logged']} predictions logged; 0 market×edge "
            f"buckets have reached the {min_n}-bet minimum yet. The warehouse is "
            f"forward-accumulating — evaluate bets pre-game and settle after games.",
            "", "_No contradictions can be asserted without data._", "",
        ]
        return "\n".join(lines)

    c = rep["contradictions"]
    lines.append(f"**{len(c)} contradiction(s)** across {rep['settled_buckets']} qualified buckets.")
    lines.append("")
    if c:
        lines.append("## Contradictions & suggested congruence updates")
        for x in c:
            lines.append(f"- **[{x['kind']}]** {x['detail']}")
            lines.append(f"  - → _{x['suggestion']}_")
        lines.append("")
    if rep["flags"]:
        lines.append("## Sustainability")
        for f in rep["flags"]:
            lines.append(f"- {f}")
        lines.append("")
    lines.append("> Recommendations only — no model/metric change is auto-applied. "
                 "Approve before editing `config.py` or pipeline weights.")
    return "\n".join(lines)


def run(min_n: int = 20, d: str | None = None) -> None:
    d = d or date.today().isoformat()
    rep = build(min_n)
    md = render_md(rep, d, min_n)

    # Vault note
    out_dir = config.VAULT_ROOT / "15-Reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    note = out_dir / f"{d}-Daily-Report.md"
    note.write_text(md, encoding="utf-8")

    # Supabase row (bot reads + posts)
    headline = (f"{len(rep['contradictions'])} contradiction(s), "
                f"{len(rep['flags'])} sustainable edge(s)"
                if rep["settled_buckets"] else "insufficient sample (forward-accumulating)")
    try:
        db.insert("daily_reports", [{
            "report_date": d, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "headline": headline, "n_contradictions": len(rep["contradictions"]),
            "contradictions": rep["contradictions"], "flags": rep["flags"], "summary_md": md,
        }])
        stored = "stored to Supabase daily_reports"
    except (Exception, SystemExit) as e:
        stored = f"Supabase store skipped ({e})"

    print(f"  Daily report {d}: {headline}")
    print(f"  Wrote {note}")
    print(f"  {stored}")


def main():
    p = argparse.ArgumentParser(description="Daily report + contradiction monitor.")
    p.add_argument("--min-n", type=int, default=20, help="Min sample to assert a contradiction")
    p.add_argument("--date", default=None, help="Report date (default today)")
    run(p.parse_args().min_n, p.parse_args().date)


if __name__ == "__main__":
    main()
