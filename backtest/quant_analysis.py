"""
Quant / biostatistics battery for the bet-evaluator's settled record.

Reads settled rows from Supabase `model_predictions` (model_probability,
market_implied_probability, won, push, clv) via the existing PostgREST client and
runs a rigorous statistical battery, degrading gracefully when the sample is thin
(every estimate prints the n required to trust it). The full theory behind each
test lives in the vault: 06-Betting-Logic/Quant-Theory-Foundations.md.

    python -m backtest.quant_analysis            # console + vault report
    python -m backtest.quant_analysis --no-write # console only

Sections:
  1. Sample inventory + statistical power (required-n for every downstream test)
  2. Calibration: Brier score + Murphy decomposition, log-loss vs market baseline
  3. Reliability table with Wilson 95% CIs per probability bucket
  4. ROI: percentile bootstrap CI + p-value vs break-even
  5. CLV beat-rate with Wilson CI (the fast-converging skill proxy)
  6. Fractional-Kelly growth simulation (full / half / quarter)
  7. SPRT sequential monitor: is the edge real yet? (go / no-go / keep-sampling)
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import date, datetime

import config

try:
    from backtest import db
except ImportError:  # run as a script from inside backtest/
    import db

random.seed(7)
Z95 = 1.959963985


# ── Statistical primitives (stdlib only) ─────────────────────────────────────


def wilson_ci(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion — accurate at small n and
    near 0/1, unlike the normal-approximation (Wald) interval."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def required_n_proportion(p: float = 0.5, half_width: float = 0.05, z: float = Z95) -> int:
    """Sample size so a proportion's 95% CI half-width <= half_width.
    n = z^2 p(1-p) / h^2 (max at p=0.5 — the conservative default)."""
    return math.ceil(z * z * p * (1 - p) / (half_width * half_width))


def bootstrap_mean_ci(xs: list[float], iters: int = 10000,
                      lo: float = 2.5, hi: float = 97.5) -> tuple[float, float, float]:
    """Percentile bootstrap CI for a mean — robust to the fat-tailed, skewed
    payoff distribution of betting ROI where the normal CI understates risk."""
    n = len(xs)
    if n == 0:
        return (0.0, 0.0, 0.0)
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += xs[random.randrange(n)]
        means.append(s / n)
    means.sort()
    point = sum(xs) / n
    return (point, means[int(lo / 100 * iters)], means[min(iters - 1, int(hi / 100 * iters))])


def bootstrap_p_value(xs: list[float], null: float = 0.0, iters: int = 10000) -> float:
    """Two-sided bootstrap p-value that the mean differs from `null` (ROI vs 0)."""
    n = len(xs)
    if n == 0:
        return 1.0
    point = sum(xs) / n
    centred = [x - point + null for x in xs]  # resample under H0
    extreme = 0
    obs = abs(point - null)
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += centred[random.randrange(n)]
        if abs(s / n - null) >= obs:
            extreme += 1
    return extreme / iters


def brier_decomposition(pairs: list[tuple[float, int]], bins: int = 10) -> dict:
    """Brier score + Murphy's reliability/resolution/uncertainty decomposition.
    BS = REL - RES + UNC.  Lower REL = better calibrated; higher RES = more
    discriminating; UNC is the irreducible base-rate variance."""
    n = len(pairs)
    if n == 0:
        return {}
    bs = sum((p - o) ** 2 for p, o in pairs) / n
    base = sum(o for _, o in pairs) / n
    unc = base * (1 - base)
    rel = res = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        grp = [(p, o) for p, o in pairs if (lo <= p < hi or (b == bins - 1 and p == 1.0))]
        if not grp:
            continue
        nk = len(grp)
        pbar = sum(p for p, _ in grp) / nk
        obar = sum(o for _, o in grp) / nk
        rel += nk * (pbar - obar) ** 2
        res += nk * (obar - base) ** 2
    return {"brier": bs, "reliability": rel / n, "resolution": res / n,
            "uncertainty": unc, "base_rate": base, "n": n}


def log_loss(pairs: list[tuple[float, int]], eps: float = 1e-15) -> float:
    if not pairs:
        return float("nan")
    s = 0.0
    for p, o in pairs:
        p = min(1 - eps, max(eps, p))
        s += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return s / len(pairs)


def sprt(k: int, n: int, p0: float, p1: float,
         alpha: float = 0.05, beta: float = 0.10) -> dict:
    """Wald's Sequential Probability Ratio Test for a binomial proportion.
    H0: true success rate = p0  vs  H1: = p1 (p1 > p0). Accumulated
    log-likelihood ratio vs Wald boundaries A (accept H1) and B (accept H0)."""
    if not (0 < p0 < 1 and 0 < p1 < 1):
        return {}
    llr = (k * math.log(p1 / p0)
           + (n - k) * math.log((1 - p1) / (1 - p0)))
    upper = math.log((1 - beta) / alpha)   # cross => accept H1 (edge is real)
    lower = math.log(beta / (1 - alpha))   # cross => accept H0 (no edge)
    if llr >= upper:
        decision = "ACCEPT H1 (edge confirmed)"
    elif llr <= lower:
        decision = "ACCEPT H0 (no edge — stop)"
    else:
        decision = "CONTINUE (keep sampling)"
    return {"llr": llr, "upper": upper, "lower": lower, "decision": decision,
            "p0": p0, "p1": p1, "k": k, "n": n}


def kelly_growth(records: list[tuple[float, float, int]],
                 fraction: float, bankroll: float = 1.0) -> dict:
    """Sequential bankroll growth under fractional Kelly.
    records = [(model_p, dec_odds, won)]. f* = (bp - q)/b, b = dec_odds-1."""
    bk = bankroll
    peak = bankroll
    max_dd = 0.0
    staked = 0
    for p, dec, won in records:
        b = dec - 1
        if b <= 0:
            continue
        edge = (b * p - (1 - p)) / b
        f = max(0.0, edge) * fraction
        if f <= 0:
            continue
        staked += 1
        stake = bk * f
        bk += stake * b if won else -stake
        peak = max(peak, bk)
        max_dd = max(max_dd, (peak - bk) / peak if peak > 0 else 0.0)
    return {"final": bk, "max_drawdown": max_dd, "bets_staked": staked,
            "growth_pct": (bk / bankroll - 1) * 100}


# ── Data ─────────────────────────────────────────────────────────────────────


def fetch_settled() -> list[dict]:
    """Settled predictions with the fields the battery needs. [] on any failure."""
    try:
        return db.select(
            "model_predictions",
            "?settled=eq.true&select=model_probability,market_implied_probability,"
            "won,push,clv,closing_implied_probability,market_type&order=prediction_time")
    except SystemExit as exc:
        print(f"  Supabase not configured ({str(exc).splitlines()[0]}).")
        return []
    except Exception as exc:  # network / schema drift — never crash the battery
        print(f"  Could not fetch settled predictions: {type(exc).__name__}: {exc}")
        return []


def _clean(rows: list[dict]) -> dict:
    """Split settled rows into the typed arrays each section consumes."""
    pairs, rois, clvs, kelly_recs, wins = [], [], [], [], 0
    graded = 0
    for r in rows:
        if r.get("push"):
            continue
        won = r.get("won")
        p = r.get("model_probability")
        imp = r.get("market_implied_probability")
        if won is None or p is None or imp is None:
            continue
        p = float(p); imp = float(imp); won = 1 if won else 0
        if not (0 < imp < 1):
            continue
        graded += 1
        wins += won
        pairs.append((p, won))
        dec = 1.0 / imp                       # decimal odds implied by the entry price
        rois.append((dec - 1) if won else -1.0)
        kelly_recs.append((p, dec, won))
        if r.get("clv") is not None:
            clvs.append(float(r["clv"]))
    return {"pairs": pairs, "rois": rois, "clvs": clvs, "kelly": kelly_recs,
            "wins": wins, "graded": graded}


# ── Report ───────────────────────────────────────────────────────────────────


def build_report(rows: list[dict]) -> str:
    L: list[str] = []
    d = _clean(rows)
    n = d["graded"]
    today = date.today().isoformat()
    L.append(f"# Quant Analysis — Bet Evaluator ({today})")
    L.append("")
    L.append("Statistical battery over settled `model_predictions`. Theory: "
             "[[../06-Betting-Logic/Quant-Theory-Foundations]].")
    L.append("")

    # 1. Inventory + power
    L.append("## 1. Sample inventory & statistical power")
    L.append(f"- Settled & graded (non-push): **{n}**")
    L.append(f"- Required n for a calibration bucket to ±5pts (95% CI): "
             f"**{required_n_proportion(0.5, 0.05)}** per bucket")
    L.append(f"- Required n for ROI mean ±2% (rough, sd≈1 unit): "
             f"**{required_n_proportion(0.5, 0.02)}**-ish overall")
    L.append(f"- CLV beat-rate to ±5pts: **{required_n_proportion(0.5, 0.05)}** settled bets")
    if n == 0:
        L.append("")
        L.append("> **No settled predictions yet.** The prospective loop began accumulating "
                 "2026-06-02; predictions settle only after `import_outcomes` + "
                 "`settle_predictions` run on finished games. The numbers above are the "
                 "sample sizes to reach before each statistic below is trustworthy.")
        L.append("")
        for s in ("2. Calibration (Brier / Murphy / log-loss)",
                  "3. Reliability table (Wilson CIs)",
                  "4. ROI bootstrap CI",
                  "5. CLV beat-rate",
                  "6. Fractional-Kelly growth",
                  "7. SPRT sequential monitor"):
            L.append(f"## {s}")
            L.append("_Insufficient sample — awaiting settled bets._")
            L.append("")
        return "\n".join(L)

    # 2. Calibration
    bd = brier_decomposition(d["pairs"])
    L.append("")
    L.append("## 2. Calibration — Brier score & Murphy decomposition")
    L.append(f"- Brier score: **{bd['brier']:.4f}**  (BS = REL − RES + UNC)")
    L.append(f"- Reliability (lower=better calibrated): **{bd['reliability']:.4f}**")
    L.append(f"- Resolution (higher=more discriminating): **{bd['resolution']:.4f}**")
    L.append(f"- Uncertainty (base-rate variance): **{bd['uncertainty']:.4f}** "
             f"(base rate {bd['base_rate']:.3f})")
    L.append(f"- Log-loss: **{log_loss(d['pairs']):.4f}**  (coin-flip baseline = "
             f"{math.log(2):.4f})")
    if n < 50:
        L.append(f"- ⚠ n={n} < 50: decomposition is noisy; treat as directional only.")

    # 3. Reliability table
    L.append("")
    L.append("## 3. Reliability table (Wilson 95% CIs)")
    L.append("| Pred. bucket | n | model avg | actual | 95% CI |")
    L.append("|---|---|---|---|---|")
    for b in range(5):
        lo, hi = b / 5, (b + 1) / 5
        grp = [(p, o) for p, o in d["pairs"] if (lo <= p < hi or (b == 4 and p == 1.0))]
        if not grp:
            continue
        k = sum(o for _, o in grp)
        nb = len(grp)
        ci = wilson_ci(k, nb)
        pavg = sum(p for p, _ in grp) / nb
        L.append(f"| {lo:.1f}–{hi:.1f} | {nb} | {pavg:.3f} | {k/nb:.3f} | "
                 f"{ci[0]:.3f}–{ci[1]:.3f} |")

    # 4. ROI bootstrap
    L.append("")
    L.append("## 4. ROI — percentile bootstrap 95% CI")
    point, blo, bhi = bootstrap_mean_ci(d["rois"])
    pval = bootstrap_p_value(d["rois"], 0.0)
    L.append(f"- Mean ROI/unit: **{point:+.4f}**  (95% CI {blo:+.4f} … {bhi:+.4f})")
    L.append(f"- Bootstrap p-value vs break-even: **{pval:.3f}** "
             f"({'significant' if pval < 0.05 else 'not significant'} at α=0.05)")
    L.append(f"- Record: {d['wins']}–{n - d['wins']} ({d['wins']/n:.1%} win rate)")

    # 5. CLV
    L.append("")
    L.append("## 5. CLV beat-rate (fast-converging skill proxy)")
    if d["clvs"]:
        beat = sum(1 for c in d["clvs"] if c > 0)
        nc = len(d["clvs"])
        ci = wilson_ci(beat, nc)
        meanclv = sum(d["clvs"]) / nc
        L.append(f"- Beat the close: **{beat}/{nc} = {beat/nc:.1%}** "
                 f"(Wilson 95% CI {ci[0]:.1%}–{ci[1]:.1%})")
        L.append(f"- Mean CLV: **{meanclv:+.4f}** implied-prob points")
        L.append("- CLV converges on skill ~4–10× faster than ROI (no outcome variance) — "
                 "trust this before trusting ROI.")
    else:
        L.append("_No closing lines captured yet — run market_data near game time so "
                 "v_closing_lines populates._")

    # 6. Kelly
    L.append("")
    L.append("## 6. Fractional-Kelly growth (from the settled record)")
    L.append("| Fraction | final bankroll | growth | max drawdown | bets |")
    L.append("|---|---|---|---|---|")
    for label, f in (("Full (1.0)", 1.0), ("Half (0.5)", 0.5), ("Quarter (0.25)", 0.25)):
        g = kelly_growth(d["kelly"], f)
        L.append(f"| {label} | {g['final']:.3f} | {g['growth_pct']:+.1f}% | "
                 f"{g['max_drawdown']:.1%} | {g['bets_staked']} |")
    L.append("Variance scales with f² but growth only with f — the quarter-Kelly drawdown "
             "should be ~¼ of full with ~¾ of the long-run growth.")

    # 7. SPRT
    L.append("")
    L.append("## 7. SPRT sequential monitor")
    s = sprt(d["wins"], n, p0=0.50, p1=0.524)  # 0.524 ≈ break-even at -110
    if s:
        L.append(f"- H0: win rate = {s['p0']:.3f} vs H1 = {s['p1']:.3f} "
                 f"(≈ -110 break-even)")
        L.append(f"- Log-likelihood ratio: **{s['llr']:+.3f}**  "
                 f"(accept-H1 ≥ {s['upper']:.2f}, accept-H0 ≤ {s['lower']:.2f})")
        L.append(f"- **Decision: {s['decision']}**")
    L.append("")
    L.append(f"_Generated {datetime.now().isoformat(timespec='seconds')}._")
    return "\n".join(L)


def _safe_print(text: str) -> None:
    """Print without dying on the Windows cp1252 console (the vault file keeps UTF-8)."""
    enc = (getattr(__import__("sys").stdout, "encoding", None) or "utf-8")
    print(text.encode(enc, "replace").decode(enc))


def run(write: bool = True) -> None:
    rows = fetch_settled()
    report = build_report(rows)
    print()
    _safe_print(report)
    if write:
        try:
            out_dir = config.VAULT_ROOT / "15-Reports"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"Quant-Analysis-{date.today().isoformat()}.md"
            path.write_text(report, encoding="utf-8")
            print(f"\n  Wrote {path}")
        except OSError as exc:
            print(f"\n  (could not write vault report: {exc})")


def main() -> None:
    p = argparse.ArgumentParser(description="Quant/biostat battery over the settled record.")
    p.add_argument("--no-write", action="store_true", help="Console only; skip the vault report.")
    args = p.parse_args()
    run(write=not args.no_write)


if __name__ == "__main__":
    main()
