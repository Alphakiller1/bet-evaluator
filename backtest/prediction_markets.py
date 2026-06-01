"""
Prediction-market ingestion — Kalshi (free, public API) MLB game-winner prices.

Kalshi is a regulated real-money exchange; its no-vig contract prices are an
independent reference to cross-check against sportsbooks and our model. We log a
timestamped snapshot per run, so movement accumulates (open -> close) the same way
sportsbook odds do — and the closing price gives a second CLV anchor.

  python -m backtest.prediction_markets            # all open MLB game markets
  python -m backtest.prediction_markets --game ARI@SEA

Series: KXMLBGAME (game winner). Ticker encodes date+teams+side; we resolve
home/away from the market title and map team codes to pipeline abbreviations.
Free, no auth. (Polymarket MLB per-game coverage is sparse — see note at bottom.)
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import config
from backtest import db
from backtest.import_snapshots import game_pk

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
MLB_GAME_SERIES = "KXMLBGAME"
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

# Kalshi team codes / sub-titles -> pipeline abbreviations (only the ones that differ).
CODE_FIX = {"AZ": "ARI", "SD": "SDP", "SF": "SFG", "TB": "TBR", "KC": "KCR",
            "WSH": "WSN", "WAS": "WSN", "CWS": "CHW", "OAK": "ATH"}
NAME_TO_ABBR = {
    "los angeles d": "LAD", "los angeles a": "LAA", "arizona": "ARI", "atlanta": "ATL",
    "baltimore": "BAL", "boston": "BOS", "chicago c": "CHC", "chicago w": "CHW",
    "cincinnati": "CIN", "cleveland": "CLE", "colorado": "COL", "detroit": "DET",
    "houston": "HOU", "kansas city": "KCR", "miami": "MIA", "milwaukee": "MIL",
    "minnesota": "MIN", "new york m": "NYM", "new york y": "NYY", "a's": "ATH",
    "athletics": "ATH", "philadelphia": "PHI", "pittsburgh": "PIT", "san diego": "SDP",
    "san francisco": "SFG", "seattle": "SEA", "st. louis": "STL", "tampa bay": "TBR",
    "texas": "TEX", "toronto": "TOR", "washington": "WSN",
}


def _get(path: str, params: dict) -> dict:
    url = f"{KALSHI_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _abbr(code_or_name: str) -> str | None:
    s = (code_or_name or "").strip()
    if not s:
        return None
    up = s.upper()
    if up in config.PARK_FACTORS:           # already a pipeline abbr
        return up
    if up in CODE_FIX:
        return CODE_FIX[up]
    n = NAME_TO_ABBR.get(s.lower())
    if n:
        return n
    return CODE_FIX.get(up, up[:3])


def _ticker_date(ticker: str) -> str | None:
    """KXMLBGAME-26JUN03... -> 2026-06-03."""
    import re
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    months = {"JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
              "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"}
    mm = months.get(mon)
    return f"20{yy}-{mm}-{dd}" if mm else None


def _implied(m: dict) -> float | None:
    bid, ask = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
    if bid is not None and ask is not None and (bid or ask):
        return round((float(bid) + float(ask)) / 2, 4)
    last = m.get("last_price_dollars")
    return round(float(last), 4) if last else None


def fetch_markets(status: str, max_pages: int = 10) -> list[dict]:
    """Paginate KXMLBGAME markets for a status ('open' or 'settled')."""
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"series_ticker": MLB_GAME_SERIES, "status": status, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = _get("/markets", params)
        page = data.get("markets", [])
        out.extend(page)
        cursor = data.get("cursor")
        if not cursor or not page:
            break
    return out


def backfill_history(max_pages: int = 60) -> None:
    """Pull the LARGE settled-market sample. The reliable signal here is the game
    OUTCOME (result/winner) — a big independent sample of real MLB results. The
    stored price (previous_* before settlement) is APPROXIMATE: many settled markets
    sit at the opening 0.50 default or a post-game extreme, so it is NOT a clean
    closing line. v_pm_calibration drops the 0/1 extremes; true historical closing
    prices require the candlesticks endpoint (price at occurrence_datetime) — TODO.
    The forward open-market scraper (run()) is the clean pre-game price + movement."""
    markets = fetch_markets("settled", max_pages=max_pages)
    rows = []
    for m in markets:
        ticker = m.get("ticker", "")
        gdate = _ticker_date(ticker)
        suffix = ticker.rsplit("-", 1)[-1]
        side = _abbr(suffix)
        result = m.get("result")            # 'yes' / 'no'
        # A settled market's last_price is the 0/1 settlement; the real CLOSING line
        # is previous_* (last meaningful price before resolution).
        pb, pa = m.get("previous_yes_bid_dollars"), m.get("previous_yes_ask_dollars")
        if pb is not None and pa is not None and (float(pb) or float(pa)):
            close_px = round((float(pb) + float(pa)) / 2, 4)
        else:
            pp = m.get("previous_price_dollars")
            close_px = round(float(pp), 4) if pp else None
        if not gdate or not side or close_px is None or result not in ("yes", "no"):
            continue
        # Resolve the opponent from the event title for a deterministic game_pk.
        core = (m.get("title") or "").split(" Winner")[0]
        away = home = None
        if " vs " in core:
            away, home = (_abbr(s.strip()) for s in core.split(" vs ", 1))
        gpk = game_pk(gdate, away, home) if away and home else game_pk(gdate, side, side)
        rows.append({
            "game_pk": gpk, "snapshot_time": (m.get("close_time") or m.get("expiration_time") or NOW),
            "game_date": gdate, "venue": "kalshi", "market_type": "ml", "selection": side,
            "implied_probability": close_px, "last_price": m.get("last_price_dollars"),
            "ticker": ticker, "source": "kalshi",
            "settled": True, "won": (result == "yes"), "result_value": m.get("expiration_value"),
        })
    if rows:
        db.insert("prediction_market_snapshots", rows)
    games = len({r["game_pk"] for r in rows})
    print(f"  Kalshi history: logged {len(rows)} settled contracts across ~{games} games "
          f"(price + outcome). Calibration base: select * from v_pm_calibration;")


def run(only_game: str | None = None) -> None:
    markets = fetch_markets("open")
    if not markets:
        raise SystemExit("  No open KXMLBGAME markets returned.")

    # Index our games by (date, unordered team pair) -> the canonical game_pk, so
    # prediction prices map to the SAME game_pk our odds/model use (robust to any
    # away/home-order disagreement between Kalshi's title and our slate).
    games_idx: dict[tuple, int] = {}
    for g in db.select("games", "?select=game_pk,game_date,home_team,away_team"):
        d = str(g.get("game_date") or "")[:10]
        a, h = g.get("away_team"), g.get("home_team")
        if d and a and h:
            games_idx[(d, frozenset((a, h)))] = g["game_pk"]

    # Group by event so we can resolve away/home from the title once per game.
    by_event: dict[str, list] = {}
    for m in markets:
        by_event.setdefault(m.get("event_ticker"), []).append(m)

    rows, games_seen = [], set()
    for event, mkts in by_event.items():
        title = (mkts[0].get("title") or "")          # "Away vs Home Winner?"
        core = title.split(" Winner")[0]
        if " vs " not in core:
            continue
        away_name, home_name = (s.strip() for s in core.split(" vs ", 1))
        away, home = _abbr(away_name), _abbr(home_name)
        if not away or not home:
            continue
        gdate = _ticker_date(event) or _ticker_date(mkts[0].get("ticker", ""))
        if not gdate:
            continue
        if only_game and f"{away}@{home}".upper() != only_game.upper():
            continue
        # Prefer our canonical game_pk (match by team pair + date); fall back to the
        # deterministic key from the title so future games still log + join later.
        gpk = games_idx.get((gdate, frozenset((away, home)))) or game_pk(gdate, away, home)
        games_seen.add(f"{away}@{home}")

        for m in mkts:
            # Ticker suffix is a clean team code (CWS, AZ, ...); prefer it over the
            # sub-title ("Chicago" is ambiguous between Cubs/White Sox).
            suffix = m.get("ticker", "").rsplit("-", 1)[-1]
            side = _abbr(suffix) or _abbr(m.get("yes_sub_title"))
            imp = _implied(m)
            if side is None or imp is None:
                continue
            rows.append({
                "game_pk": gpk, "snapshot_time": NOW, "venue": "kalshi",
                "market_type": "ml", "selection": side, "line": None,
                "yes_bid": m.get("yes_bid_dollars"), "yes_ask": m.get("yes_ask_dollars"),
                "last_price": m.get("last_price_dollars"), "implied_probability": imp,
                "volume": m.get("volume_fp"), "open_interest": m.get("open_interest_fp"),
                "liquidity": m.get("liquidity_dollars"), "ticker": m.get("ticker"),
                "source": "kalshi",
            })

    if rows:
        db.insert("prediction_market_snapshots", rows)
    print(f"  Kalshi: logged {len(rows)} contract prices across {len(games_seen)} games "
          f"(venue=kalshi, market=ml) at {NOW}.")
    if rows:
        print("  Re-run through the day to accumulate movement; the closing price is a "
              "second CLV anchor. Cross-reference: select * from v_market_consensus;")


def main():
    p = argparse.ArgumentParser(description="Kalshi MLB prediction-market ingestion.")
    p.add_argument("--game", help='Limit to "AWAY@HOME"')
    p.add_argument("--history", action="store_true",
                   help="Backfill the LARGE settled-market sample (closing price + outcome)")
    p.add_argument("--pages", type=int, default=60, help="Max history pages (200/page)")
    args = p.parse_args()
    if args.history:
        backfill_history(max_pages=args.pages)
    else:
        run(args.game)


if __name__ == "__main__":
    main()
