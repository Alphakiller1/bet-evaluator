"""
Bet Evaluator -- turns a single bet into a structured analysis.

Standalone project. READS MLB data from the mlbma_pipeline output folder (never
writes to it) and WRITES the analysis as a note into the ChaseAnalytics-Brain vault.

Given a bet (game, market, side, line, American odds) this module:

  1. Pulls that game's offense/pitching metrics, fired signals, and convergence
     from the pipeline's daily CSV outputs (read-only).
  2. Builds an expected-runs model for both teams (offense x opposing SP x park),
     and derives a statistical probability for the bet's market.
  3. Runs the value layer (implied prob -> edge -> EV -> fair odds -> unit size).
  4. Computes a variance / risk profile from ~2.4k historical game results.
  5. Renders a worded analysis (thesis / implications / risk / variance / verdict)
     and writes it into the vault bet history.

IMPORTANT -- this is a TRANSPARENT HEURISTIC, not a backtested model. The win
probability is anchored on league base rates and metric edges; it is NOT yet
calibrated against settled results. Treat the probability as a structured prior,
not proof of edge. The vault bet-history + CLV log is the loop that calibrates it.
See 06-Betting-Logic/Win-Probability-Model in the vault.

Usage:
    python bet_evaluator.py --game "TOR@BAL" --market ml --side BAL --odds -130
    python bet_evaluator.py --game "ATL@CIN" --market total --side over --line 9.5 --odds -110
    python bet_evaluator.py --game "NYY@ATH" --market team_total --side NYY --ou over --line 4.5 --odds +100
    python bet_evaluator.py --game "PHI@LAD" --market runline --side LAD --line -1.5 --odds +105
"""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

import config
import market_data
from config import (
    AWAY_BASE_WINP,
    BET_HISTORY_DIR,
    CONFIDENCE_TIERS,
    HFA_RUNS,
    HOME_BASE_WINP,
    IMPLAUSIBLE_EDGE,
    LEAGUE_FIP,
    LEAGUE_RUNS_PER_TEAM,
    MARGIN_SD,
    OFF_FACTOR_CLIP,
    OSI_RUN_SENSITIVITY,
    PIPELINE_DATA_DIR,
    PITCH_FACTOR_CLIP,
    REGRESSION_TO_MEAN,
    SP_FIP_WEIGHT,
    TEAM_RUNS_SD,
    TOTAL_RUNS_SD,
    park_factor_for_team,
)


# ── Data loading (read-only from pipeline) ───────────────────────────────────


def load(filename: str) -> pd.DataFrame | None:
    path = os.path.join(PIPELINE_DATA_DIR, filename)
    if not os.path.exists(path):
        print(f"  WARNING: {filename} not found in {PIPELINE_DATA_DIR}")
        return None
    return pd.read_csv(path)


# ── Odds math ──────────────────────────────────────────────────────────────


def american_to_decimal(odds: int) -> float:
    return 1 + (odds / 100.0) if odds > 0 else 1 + (100.0 / -odds)


def american_to_implied(odds: int) -> float:
    return 100.0 / (odds + 100.0) if odds > 0 else (-odds) / (-odds + 100.0)


def prob_to_american(p: float) -> int:
    p = min(max(p, 1e-4), 1 - 1e-4)
    if p >= 0.5:
        return -round(100 * p / (1 - p))
    return round(100 * (1 - p) / p)


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _fmt(v: float | None, nd: int = 2) -> str:
    return f"{v:.{nd}f}" if v is not None else "n/a"


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class GameData:
    away: str
    home: str
    away_sp: str
    home_sp: str
    away_hand: str
    home_hand: str
    away_osi: float | None       # lineup OSI vs opposing SP (from today_matchups)
    home_osi: float | None
    away_fip: float | None
    home_fip: float | None
    away_hr9: float | None
    home_hr9: float | None
    away_k: float | None
    home_k: float | None
    park_factor: float = 1.0
    weather: dict[str, Any] = field(default_factory=dict)


@dataclass
class Probabilities:
    exp_away_runs: float
    exp_home_runs: float
    exp_total: float
    exp_margin: float            # home - away
    p_home_win: float
    p_away_win: float


def _num(v) -> float | None:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(v) -> float | None:
    """K%/BB% may be stored as fraction (0.24) or points (24); return points."""
    n = _num(v)
    if n is None:
        return None
    return n * 100 if n <= 1.5 else n


# ── Loaders ──────────────────────────────────────────────────────────────────


def load_game(away: str, home: str) -> GameData:
    matchups = load("today_matchups.csv")
    if matchups is None:
        raise FileNotFoundError("today_matchups.csv not found -- run the pipeline first.")
    matchups["Away"] = matchups["Away"].astype(str).str.upper().str.strip()
    matchups["Home"] = matchups["Home"].astype(str).str.upper().str.strip()
    row = matchups[(matchups["Away"] == away) & (matchups["Home"] == home)]
    if row.empty:
        slate = ", ".join(f"{a}@{h}" for a, h in zip(matchups["Away"], matchups["Home"]))
        raise ValueError(f"{away}@{home} not on today's slate. Available: {slate}")
    g = row.iloc[0]

    weather = {}
    wx = load("today_weather.csv")
    if wx is not None and "home_team" in wx.columns:
        wx["home_team"] = wx["home_team"].astype(str).str.upper().str.strip()
        wrow = wx[wx["home_team"] == home]
        if not wrow.empty:
            w = wrow.iloc[0]
            weather = {
                "temp_f": _num(w.get("temperature_f")),
                "wind_mph": _num(w.get("wind_speed_mph")),
                "wind_dir": str(w.get("wind_direction", "")),
                "conditions": str(w.get("conditions", "")),
                "dome": bool(w.get("is_dome", False)),
            }

    return GameData(
        away=away,
        home=home,
        away_sp=str(g.get("Away_SP", "TBD")),
        home_sp=str(g.get("Home_SP", "TBD")),
        away_hand=str(g.get("Away_Hand", "R")).upper()[:1] or "R",
        home_hand=str(g.get("Home_Hand", "R")).upper()[:1] or "R",
        away_osi=_num(g.get("Away_OSI")),
        home_osi=_num(g.get("Home_OSI")),
        away_fip=_num(g.get("Away_FIP")),
        home_fip=_num(g.get("Home_FIP")),
        away_hr9=_num(g.get("Away_HR9")),
        home_hr9=_num(g.get("Home_HR9")),
        away_k=_pct(g.get("Away_K%")),
        home_k=_pct(g.get("Home_K%")),
        park_factor=park_factor_for_team(home),
        weather=weather,
    )


def load_signals_for_game(away: str, home: str) -> dict[str, list[dict]]:
    """Return {'away': [fired signal dicts], 'home': [...]} for this game."""
    sig = load("signals_today.csv")
    out = {"away": [], "home": []}
    if sig is None:
        return out
    sig["away"] = sig["away"].astype(str).str.upper().str.strip()
    sig["home"] = sig["home"].astype(str).str.upper().str.strip()
    rows = sig[(sig["away"] == away) & (sig["home"] == home)]
    for _, r in rows.iterrows():
        fired = str(r.get("fired")).strip().lower() in ("true", "1")
        if not fired:
            continue
        side = str(r.get("side", "")).strip().lower()
        if side in out:
            out[side].append({
                "name": r.get("signal_name"),
                "direction": r.get("direction"),
                "magnitude": _num(r.get("magnitude")),
                "bet_angle": r.get("bet_angle"),
                "verdict": r.get("verdict_text"),
            })
    return out


def load_convergence_for_game(away: str, home: str) -> dict[str, dict]:
    conv = load("signals_convergence.csv")
    out = {}
    if conv is None:
        return out
    conv["away"] = conv["away"].astype(str).str.upper().str.strip()
    conv["home"] = conv["home"].astype(str).str.upper().str.strip()
    rows = conv[(conv["away"] == away) & (conv["home"] == home)]
    for _, r in rows.iterrows():
        out[str(r.get("side", "")).strip().lower()] = {
            "count": _num(r.get("convergence_count")),
            "direction": r.get("convergence_direction"),
            "is_play": str(r.get("is_convergence_play")).strip().lower() in ("true", "1"),
            "fired": _num(r.get("signals_fired")),
        }
    return out


def refresh_anchors() -> dict[str, float]:
    """Recompute league base rates / variance from game_results.csv if present."""
    anchors = {
        "home_winp": HOME_BASE_WINP, "away_winp": AWAY_BASE_WINP,
        "league_runs": LEAGUE_RUNS_PER_TEAM, "total_sd": TOTAL_RUNS_SD,
        "team_sd": TEAM_RUNS_SD, "margin_sd": MARGIN_SD, "blown_save": 0.172,
    }
    g = load("game_results.csv")
    if g is None or g.empty:
        return anchors
    try:
        home = g[g["home_away"] == "home"]
        anchors["home_winp"] = round(home["result"].eq("W").mean(), 4)
        anchors["away_winp"] = round(g[g["home_away"] == "away"]["result"].eq("W").mean(), 4)
        anchors["league_runs"] = round(g["team_runs"].mean(), 3)
        gt = home["team_runs"] + home["opp_runs"]
        anchors["total_sd"] = round(gt.std(), 3)
        anchors["team_sd"] = round(g["team_runs"].std(), 3)
        anchors["margin_sd"] = round((home["team_runs"] - home["opp_runs"]).std(), 3)
        if "blown_save" in g.columns:
            anchors["blown_save"] = round(g["blown_save"].mean(), 4)
    except Exception as exc:  # never let stats break an eval
        print(f"  (anchors: using defaults -- {exc})")
    return anchors


# ── Probability model ──────────────────────────────────────────────────────


def _regress(factor: float) -> float:
    """Pull a multiplier toward 1.0 to temper noisy early-season extremes."""
    return 1 + (factor - 1) * (1 - REGRESSION_TO_MEAN)


def offense_factor(osi: float | None) -> float:
    if osi is None:
        return 1.0
    raw = clip(1 + (osi - 50) / 100 * OSI_RUN_SENSITIVITY, *OFF_FACTOR_CLIP)
    return _regress(raw)


def pitch_factor(opp_sp_fip: float | None) -> float:
    """Run-allowance multiplier from the opposing starter, blended toward league
    for the bullpen innings the SP does not cover. >1 = allows more runs."""
    if opp_sp_fip is None:
        return 1.0
    sp = clip(opp_sp_fip / LEAGUE_FIP, *PITCH_FACTOR_CLIP)
    blended = SP_FIP_WEIGHT * sp + (1 - SP_FIP_WEIGHT) * 1.0
    return _regress(blended)


def model_probabilities(gd: GameData, anchors: dict[str, float]) -> Probabilities:
    league = anchors["league_runs"]
    # Away offense vs home SP; home offense vs away SP.
    exp_away = league * offense_factor(gd.away_osi) * pitch_factor(gd.home_fip) * gd.park_factor
    exp_home = league * offense_factor(gd.home_osi) * pitch_factor(gd.away_fip) * gd.park_factor
    exp_home += HFA_RUNS
    exp_total = exp_away + exp_home
    exp_margin = exp_home - exp_away

    sd = anchors["margin_sd"]
    p_home_model = normal_cdf(exp_margin / sd)
    # Blend toward empirical home base rate (regularizes thin metric edges).
    base = anchors["home_winp"] / (anchors["home_winp"] + anchors["away_winp"])
    p_home = clip(0.85 * p_home_model + 0.15 * base, 0.05, 0.95)
    return Probabilities(
        exp_away_runs=round(exp_away, 2),
        exp_home_runs=round(exp_home, 2),
        exp_total=round(exp_total, 2),
        exp_margin=round(exp_margin, 2),
        p_home_win=round(p_home, 4),
        p_away_win=round(1 - p_home, 4),
    )


def market_probability(market: str, side: str, line: float | None,
                       gd: GameData, probs: Probabilities,
                       anchors: dict[str, float], ou: str | None) -> tuple[float, str]:
    """Return (probability_of_bet_hitting, human description of the pick)."""
    market = market.lower()
    if market == "ml":
        team = _resolve_side_team(side, gd)
        if team == gd.home:
            return probs.p_home_win, f"{gd.home} ML"
        return probs.p_away_win, f"{gd.away} ML"

    if market == "total":
        if line is None:
            raise ValueError("total market requires --line")
        p_over = 1 - normal_cdf((line - probs.exp_total) / anchors["total_sd"])
        if side.lower() == "over":
            return p_over, f"Over {line}"
        return 1 - p_over, f"Under {line}"

    if market == "team_total":
        if line is None:
            raise ValueError("team_total market requires --line")
        team = _resolve_side_team(side, gd)
        exp = probs.exp_home_runs if team == gd.home else probs.exp_away_runs
        p_over = 1 - normal_cdf((line - exp) / anchors["team_sd"])
        direction = (ou or "over").lower()
        if direction == "over":
            return p_over, f"{team} team total Over {line}"
        return 1 - p_over, f"{team} team total Under {line}"

    if market == "runline":
        if line is None:
            raise ValueError("runline market requires --line, e.g. -1.5 or +1.5")
        team = _resolve_side_team(side, gd)
        # Margin from the perspective of the chosen team.
        team_margin = probs.exp_margin if team == gd.home else -probs.exp_margin
        # Cover if team_margin + line > 0  =>  margin > -line
        threshold = -line
        p_cover = 1 - normal_cdf((threshold - team_margin) / anchors["margin_sd"])
        return p_cover, f"{team} {line:+g}"

    raise ValueError(f"Unknown market '{market}'. Use ml|total|team_total|runline.")


def _resolve_side_team(side: str, gd: GameData) -> str:
    s = side.strip().upper()
    if s in ("HOME", gd.home):
        return gd.home
    if s in ("AWAY", gd.away):
        return gd.away
    raise ValueError(f"side '{side}' must be {gd.away}, {gd.home}, home, or away.")


# ── Value + sizing ─────────────────────────────────────────────────────────


def value_layer(model_p: float, odds: int) -> dict[str, Any]:
    implied = american_to_implied(odds)
    decimal = american_to_decimal(odds)
    edge = model_p - implied
    ev = model_p * (decimal - 1) - (1 - model_p)          # per $1 staked
    b = decimal - 1
    kelly = (b * model_p - (1 - model_p)) / b if b > 0 else 0.0
    quarter_kelly = max(0.0, kelly / 4)

    tier, units = "Pass", "0u"
    for edge_min, t_label, u in CONFIDENCE_TIERS:
        if edge >= edge_min:
            tier, units = t_label, u
            break

    implausible = edge >= IMPLAUSIBLE_EDGE
    if implausible:
        # Don't auto-size a too-good-to-be-true edge; demand a manual input check.
        tier, units = "Review", "verify inputs"
        verdict = "REVIEW"
    elif edge >= 0.020 and ev > 0:
        verdict = "PLAY"
    else:
        verdict = "PASS"

    return {
        "implied": round(implied, 4),
        "decimal": round(decimal, 3),
        "edge": round(edge, 4),
        "ev_per_unit": round(ev, 4),
        "fair_odds": prob_to_american(model_p),
        "kelly_full": round(kelly, 4),
        "kelly_quarter": round(quarter_kelly, 4),
        "tier": tier,
        "units": units,
        "implausible": implausible,
        "verdict": verdict,
    }


# ── Variance / risk layer ────────────────────────────────────────────────────


def risk_layer(gd: GameData, market: str, anchors: dict[str, float]) -> list[str]:
    notes: list[str] = []
    tp = load("team_profiles.csv")
    prof = {}
    if tp is not None and "team" in tp.columns:
        tp["team"] = tp["team"].astype(str).str.upper().str.strip()
        for t in (gd.away, gd.home):
            r = tp[tp["team"] == t]
            if not r.empty:
                prof[t] = r.iloc[0]

    # Pitcher variance: HR-prone and low-K starters add tail risk.
    for label, hr9, k, sp in (
        (gd.home, gd.home_hr9, gd.home_k, gd.home_sp),
        (gd.away, gd.away_hr9, gd.away_k, gd.away_sp),
    ):
        if hr9 is not None and hr9 >= 1.5:
            notes.append(f"{sp} ({label}) is HR-prone (HR/9 {hr9:.2f}) -- crooked-number / boom-bust risk.")
        if k is not None and k <= 18.0:
            notes.append(f"{sp} ({label}) is a low-K arm (K% {k:.1f}%) -- contact-dependent, BABIP variance.")

    # Bullpen exposure.
    for t in (gd.away, gd.home):
        p = prof.get(t)
        if p is not None:
            bp = _num(p.get("bullpen_era"))
            wd = str(p.get("window_direction", "")).strip()
            if bp is not None and bp >= 4.50:
                notes.append(f"{t} bullpen shaky (pen ERA {bp:.2f}) -- late-inning leverage risk.")
            if wd in ("rising", "falling"):
                arrow = "trending up" if wd == "rising" else "trending down"
                notes.append(f"{t} offense {arrow} (window_direction={wd}).")

    notes.append(
        f"League blown-save rate ~{anchors['blown_save']*100:.0f}% and mean run margin "
        f"~{anchors['margin_sd']:.1f} -- single-game baseball variance is high regardless of edge."
    )

    # Weather (totals-relevant).
    w = gd.weather
    if w and not w.get("dome"):
        wind = w.get("wind_mph")
        if wind is not None and wind >= 12:
            notes.append(f"Wind {wind:.0f} mph {w.get('wind_dir','')} at {gd.home} -- ball-flight / total swing factor.")
        temp = w.get("temp_f")
        if temp is not None and temp <= 50:
            notes.append(f"Cold ({temp:.0f}F) -- suppresses offense, leans Under.")
    elif w.get("dome"):
        notes.append("Dome / roof -- weather neutral.")

    return notes


# ── Rendering ────────────────────────────────────────────────────────────────


def resolve_side_key(market: str, side: str, gd: GameData, ou: str | None) -> str:
    """The 'side' string as stored in the market snapshot for this bet."""
    m = market.lower()
    if m == "total":
        return side.lower()
    if m == "team_total":
        return f"{_resolve_side_team(side, gd)}_{(ou or 'over').lower()}"
    return _resolve_side_team(side, gd)   # ml / runline -> team abbr


def lookup_market(gd: GameData, market: str, side: str, line: float | None,
                  ou: str | None) -> dict[str, Any] | None:
    """Best price + line movement for this bet from the scraped snapshot."""
    side_key = resolve_side_key(market, side, gd, ou)
    bp = market_data.best_price(gd.away, gd.home, market, side_key, line)
    if bp is None:
        return None
    mv = market_data.line_movement(gd.away, gd.home, market, side_key, line)
    return {"best": bp, "movement": mv}


def build_analysis(gd: GameData, market: str, side: str, line: float | None,
                   ou: str | None, odds: int,
                   market_info: dict[str, Any] | None = None) -> dict[str, Any]:
    anchors = refresh_anchors()
    probs = model_probabilities(gd, anchors)
    model_p, pick_desc = market_probability(market, side, line, gd, probs, anchors, ou)
    model_p = clip(model_p, 0.02, 0.98)
    value = value_layer(model_p, odds)
    risks = risk_layer(gd, market, anchors)
    if value["implausible"]:
        risks.insert(0, (
            f"⚠️ Edge of {value['edge']*100:+.1f} pts is implausibly large — almost always means "
            "noisy/early-season inputs (esp. SP FIP) or a misread line, not a real edge. "
            "Verify the matchup data and the posted number before sizing."
        ))
    signals = load_signals_for_game(gd.away, gd.home)
    conv = load_convergence_for_game(gd.away, gd.home)
    return {
        "game": gd, "market": market, "side": side, "line": line, "ou": ou,
        "odds": odds, "pick_desc": pick_desc, "anchors": anchors, "probs": probs,
        "model_p": round(model_p, 4), "value": value, "risks": risks,
        "signals": signals, "convergence": conv, "market_info": market_info,
    }


def render_markdown(a: dict[str, Any]) -> str:
    gd: GameData = a["game"]
    probs: Probabilities = a["probs"]
    v = a["value"]
    today = date.today().isoformat()
    pick = a["pick_desc"]
    odds = a["odds"]
    odds_str = f"{odds:+d}"
    edge_pct = v["edge"] * 100
    model_pct = a["model_p"] * 100
    implied_pct = v["implied"] * 100

    # Convergence summary for the relevant offensive side(s).
    conv_lines = []
    for side in ("away", "home"):
        c = a["convergence"].get(side)
        if c:
            team = gd.away if side == "away" else gd.home
            flag = " **[CONVERGENCE PLAY]**" if c["is_play"] else ""
            conv_lines.append(
                f"- **{team}** ({side}): {int(c['fired'] or 0)} signals fired, "
                f"weighted {c['count']:.0f}, direction *{c['direction']}*{flag}"
            )

    sig_lines = []
    for side in ("away", "home"):
        team = gd.away if side == "away" else gd.home
        for s in a["signals"].get(side, []):
            sig_lines.append(f"- *{team} — {s['name']}* ({s['direction']}): {s['verdict']}")

    risk_lines = "\n".join(f"- {r}" for r in a["risks"]) or "- (none flagged)"
    sig_block = "\n".join(sig_lines) or "- No signals fired for this game."
    conv_block = "\n".join(conv_lines) or "- No convergence data."

    weather_str = "n/a"
    if gd.weather:
        w = gd.weather
        if w.get("dome"):
            weather_str = "Dome"
        else:
            temp = w.get("temp_f")
            wind = w.get("wind_mph")
            temp_s = f"{temp:.0f}F" if temp is not None else "?F"
            wind_s = f"{wind:.0f} mph" if wind is not None else "? mph"
            weather_str = f"{temp_s}, wind {wind_s} {w.get('wind_dir','')}, {w.get('conditions','')}".strip()

    verdict_emoji = {"PLAY": "✅", "REVIEW": "⚠️"}.get(v["verdict"], "🚫")

    return f"""---
title: "Bet Eval — {gd.away}@{gd.home} {pick}"
tags: [bet, evaluation, mlb]
date: {today}
sport: MLB
game: "{gd.away}@{gd.home}"
market: {a['market']}
pick: "{pick}"
odds: {odds}
model_prob: {a['model_p']}
implied_prob: {v['implied']}
edge: {v['edge']}
ev_per_unit: {v['ev_per_unit']}
confidence_tier: {v['tier']}
units: "{v['units']}"
verdict: {v['verdict']}
status: status/pending
result: ""
closing_odds: ""
clv: ""
---

# {verdict_emoji} {pick} @ {odds_str} — **{v['verdict']}**

> **Model {model_pct:.1f}%** vs **Implied {implied_pct:.1f}%** → **Edge {edge_pct:+.1f} pts** ·
> EV **{v['ev_per_unit']:+.3f}/unit** · Fair odds **{v['fair_odds']:+d}** ·
> Tier **{v['tier']}** → size **{v['units']}** (¼-Kelly {v['kelly_quarter']*100:.1f}%)

## Matchup
- **{gd.away}** ({gd.away_sp}, {gd.away_hand}HP, FIP {_fmt(gd.away_fip)}) lineup OSI **{gd.away_osi}**
- **{gd.home}** ({gd.home_sp}, {gd.home_hand}HP, FIP {_fmt(gd.home_fip)}) lineup OSI **{gd.home_osi}**
- Park factor (home): **{gd.park_factor}** · Weather: {weather_str}

## Market
{_market_block(a)}

## Statistical Probability
| | Away ({gd.away}) | Home ({gd.home}) |
|---|---|---|
| Expected runs | {probs.exp_away_runs} | {probs.exp_home_runs} |
| Win probability | {probs.p_away_win*100:.1f}% | {probs.p_home_win*100:.1f}% |

- Expected total: **{probs.exp_total}** · Expected margin (home−away): **{probs.exp_margin:+.2f}**
- **This bet ({pick}) model probability: {model_pct:.1f}%**

## Thesis
{pick} prices in **{implied_pct:.1f}%** at {odds_str}. The expected-runs model
(offense OSI × opposing-SP FIP × park, anchored on league base rates) puts the true
likelihood at **{model_pct:.1f}%**, an edge of **{edge_pct:+.1f} points**.
{_thesis_sentence(a)}

## Pipeline Signals Fired
{sig_block}

### Convergence
{conv_block}

## Implications
{_implications(a)}

## Risk Factors
{risk_lines}

## Variance Factors
- Single-game MLB outcomes carry ~{a['anchors']['margin_sd']:.1f} runs of margin SD and
  ~{a['anchors']['total_sd']:.1f} runs of total SD — a real edge still loses often game-to-game.
- Model is a **heuristic prior**, not calibrated to settled results. Log the result + closing
  line below so the [[Win-Probability-Model]] calibration board can grade it.

## Verdict
**{v['verdict']} — {pick} @ {odds_str}**, suggested size **{v['units']}** ({v['tier']} tier).
{_verdict_sentence(a)}

---
*Generated by the bet-evaluator project (`bet_evaluator.py`). Edit `result`, `closing_odds`,
`clv` after settlement. See [[Bet-Evaluation-Framework]] · [[_Bet-History-Hub]].*
"""


def _market_block(a: dict) -> str:
    mi = a.get("market_info")
    odds = a["odds"]
    if not mi:
        return (f"- Priced at **{odds:+d}** (manual input — no scraped market for this bet). "
                "Run `python market_data.py --fetch` to enable best-price + line movement.")
    bp = mi["best"]
    lines = [f"- Best available: **{bp['odds']:+d} @ {bp['book']}** "
             f"across {bp['n_books']} book(s)."]
    mv = mi.get("movement")
    if mv and mv["snapshots"] > 1:
        drift = "toward this side" if mv["delta"] > 0 else "against this side"
        lines.append(f"- Line movement: open **{mv['open']:+d}** → now **{mv['current']:+d}** "
                     f"(Δ{mv['delta']:+d}, {drift}) over {mv['snapshots']} snapshots.")
    elif mv:
        lines.append("- Line movement: first snapshot recorded — re-fetch over time to track drift.")
    return "\n".join(lines)


def _thesis_sentence(a: dict) -> str:
    v = a["value"]
    if v["verdict"] == "REVIEW":
        return ("This edge is too large to trust at face value — treat it as a flag to verify the "
                "inputs and the line, not as a green light to bet big.")
    if v["verdict"] == "PLAY":
        return ("The metric edge and price disagree in your favor — this clears the value "
                "threshold and is worth a position.")
    if v["edge"] > 0:
        return ("There is a slim positive edge, but it does not clear the noise/vig threshold "
                "to justify a position with confidence.")
    return ("The price is efficient or against you — the market already reflects (or overstates) "
            "this side. No edge.")


def _implications(a: dict) -> str:
    gd: GameData = a["game"]
    probs: Probabilities = a["probs"]
    lines = []
    if a["market"] == "total":
        lean = "Over" if probs.exp_total >= (a["line"] or probs.exp_total) else "Under"
        lines.append(f"- Model expects **{probs.exp_total} total runs** vs a {a['line']} line → leans **{lean}**.")
    if a["market"] in ("ml", "runline"):
        fav = gd.home if probs.exp_margin > 0 else gd.away
        lines.append(f"- Expected margin favors **{fav}** by {abs(probs.exp_margin):.1f} runs.")
    # Convergence alignment.
    for side in ("away", "home"):
        c = a["convergence"].get(side)
        if c and c["is_play"]:
            team = gd.away if side == "away" else gd.home
            lines.append(f"- {team} is a **convergence play** ({c['direction']}) — multiple independent signals agree.")
    if not lines:
        lines.append("- No strong directional implication beyond the headline edge.")
    return "\n".join(lines)


def _verdict_sentence(a: dict) -> str:
    v = a["value"]
    if v["verdict"] == "REVIEW":
        return ("Do not auto-size. Confirm the SP, lineup, and posted line are current; if the edge "
                "holds after a real data check, size conservatively (Lean at most).")
    if v["verdict"] == "PLAY":
        return ("Take the price now; record the closing line later to grade CLV — beating the "
                "close validates the process even when the bet loses.")
    return "Skip or wait for a better number. Re-evaluate if the line moves toward fair value."


# ── Output / persistence ─────────────────────────────────────────────────────


def write_to_vault(a: dict[str, Any], md: str) -> Path:
    BET_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    gd: GameData = a["game"]
    safe_pick = a["pick_desc"].replace(" ", "-").replace("/", "-")
    fname = f"{date.today().isoformat()}-{gd.away}@{gd.home}-{safe_pick}.md"
    path = BET_HISTORY_DIR / fname
    path.write_text(md, encoding="utf-8")
    return path


def print_summary(a: dict[str, Any]) -> None:
    v = a["value"]
    gd: GameData = a["game"]
    print()
    print(f"  {'='*58}")
    print(f"  {gd.away}@{gd.home}  |  {a['pick_desc']}  @ {a['odds']:+d}")
    print(f"  {'='*58}")
    print(f"  Model prob   : {a['model_p']*100:5.1f}%")
    print(f"  Implied prob : {v['implied']*100:5.1f}%")
    print(f"  Edge         : {v['edge']*100:+5.1f} pts")
    print(f"  EV / unit    : {v['ev_per_unit']:+.3f}")
    print(f"  Fair odds    : {v['fair_odds']:+d}")
    print(f"  Tier / size  : {v['tier']}  ->  {v['units']}  (1/4-Kelly {v['kelly_quarter']*100:.1f}%)")
    print(f"  VERDICT      : {v['verdict']}")
    print(f"  {'='*58}")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(description="Evaluate a single MLB bet.")
    p.add_argument("--game", required=True, help='Matchup as "AWAY@HOME", e.g. "TOR@BAL"')
    p.add_argument("--market", required=True, choices=["ml", "total", "team_total", "runline"])
    p.add_argument("--side", required=True, help="Team abbr / home / away / over / under")
    p.add_argument("--line", type=float, default=None, help="Total / team-total / run-line number")
    p.add_argument("--ou", choices=["over", "under"], default=None, help="For team_total: over or under")
    p.add_argument("--odds", type=int, default=None,
                   help="American odds (e.g. -130). Omit to live-fetch best price from the market.")
    p.add_argument("--no-fetch", action="store_true",
                   help="Don't hit the odds API; use the cached snapshot only")
    p.add_argument("--props", action="store_true",
                   help="Also pull props/team totals on the live fetch (costs more credits)")
    p.add_argument("--no-write", action="store_true", help="Print only; do not write a vault note")
    p.add_argument("--no-log", action="store_true",
                   help="Don't log this prediction to Supabase (model_predictions)")
    args = p.parse_args()

    if "@" not in args.game:
        raise SystemExit('--game must be "AWAY@HOME", e.g. "TOR@BAL"')
    away, home = (s.strip().upper() for s in args.game.split("@", 1))

    gd = load_game(away, home)

    # On-demand: when no manual odds are given, pull live odds for THIS game now
    # (and store the snapshot for line-movement / closing-line history).
    if args.odds is None and not args.no_fetch:
        try:
            market_data.fetch_game(away, home, props=args.props)
        except SystemExit as e:
            print(f"  (live odds fetch skipped -- {e})")

    # Resolve odds: explicit flag wins; otherwise best price from the snapshot.
    market_info = lookup_market(gd, args.market, args.side, args.line, args.ou)
    odds = args.odds
    if odds is None:
        if market_info is None:
            raise SystemExit(
                "No --odds given and no market data for this bet.\n"
                "  Pass --odds, or ensure the game is on the live board.")
        odds = market_info["best"]["odds"]
        print(f"  Using best price: {odds:+d} @ {market_info['best']['book']}")

    a = build_analysis(gd, args.market, args.side, args.line, args.ou, odds, market_info)
    print_summary(a)
    if not args.no_log:
        from backtest import log_prediction
        gpk = log_prediction.log(a)
        if gpk:
            print(f"  Logged prediction to Supabase (game_pk {gpk}).")
    md = render_markdown(a)
    if not args.no_write:
        path = write_to_vault(a, md)
        print(f"  Wrote: {path}")


if __name__ == "__main__":
    main()
