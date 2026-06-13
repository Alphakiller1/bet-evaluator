"""
chase - unified CLI hub for the whole toolkit. One entry point that dispatches to the
model tools and the sharp/market tools (each of which keeps its own standalone file).

    python chase.py <command> [args...]

Commands
  game AWAY@HOME [--k-line N]   per-game report: ML / Total / F5 / Pitcher Ks
  value [--min N] [--top N]     today's value board (ranked slate)
  edge  [--min-n N]             profit-focused market-edge scan (the headline)
  cross [--min-edge N]          Kalshi vs commercial books: arb / value / thin
  book  [--min-n N]             cross-book intelligence (sharp vs soft)
  scenarios [--min-n N]         parameterized react-scenarios
  regress [--game AWAY@HOME]    pitcher regression / progression spots (fade hot, back cold)
  ingest                        bank today's pre-game snapshots + MLBMA signals into Supabase
  signals                       ingest today's cross-metric signals into Supabase only
  serve                         launch the visual Command Center (http://localhost:8787)
  sharp                         fetch + report live sharp signals (uses API credits)
  predict                       fetch live Kalshi contract prices
  model [--min-n N]             model calibration / ROI / CLV
  quant [--no-write]            full quant/biostat battery (Brier, Kelly, SPRT, CLV)
  report                        daily contradiction report -> vault + Supabase
  bet ...                       single-bet evaluator (passes through to bet_evaluator)

Examples
  python chase.py value
  python chase.py game LAD@ARI --k-line 6.5
  python chase.py edge
"""

from __future__ import annotations

import sys

USAGE = __doc__


def _dispatch(cmd: str, rest: list[str]):
    # each tool parses its own argv; hand it the remaining args
    sys.argv = [cmd] + rest

    if cmd == "game":
        import game_report
        # game_report wants --game; allow bare "AWAY@HOME" as the first arg
        if rest and "@" in rest[0] and not rest[0].startswith("-"):
            sys.argv = [cmd, "--game", rest[0]] + rest[1:]
        game_report.main()
    elif cmd == "value":
        from backtest import value_board; value_board.main()
    elif cmd == "edge":
        from backtest import market_edge; market_edge.main()
    elif cmd == "cross":
        from backtest import cross_venue; cross_venue.main()
    elif cmd == "book":
        from backtest import book_intel; book_intel.main()
    elif cmd == "scenarios":
        from backtest import scenarios; scenarios.main()
    elif cmd == "sharp":
        import sharp_tracker; sharp_tracker.main()
    elif cmd == "predict":
        from backtest import prediction_markets; prediction_markets.main()
    elif cmd == "model":
        from backtest import analyze_model; analyze_model.main()
    elif cmd == "quant":
        from backtest import quant_analysis; quant_analysis.main()
    elif cmd == "report":
        from backtest import daily_report; daily_report.main()
    elif cmd in ("serve", "ui", "center"):
        import command_center; command_center.main()
    elif cmd == "regress":
        import regression; regression.main()
    elif cmd == "ingest":
        from backtest import import_snapshots, import_signals
        import_snapshots.run()
        import_signals.run()
    elif cmd == "signals":
        from backtest import import_signals; import_signals.run()
    elif cmd == "bet":
        import bet_evaluator; bet_evaluator.main()
    else:
        print(USAGE)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return
    _dispatch(sys.argv[1], sys.argv[2:])


if __name__ == "__main__":
    main()
