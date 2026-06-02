# Chase — Developing the System (outside Claude Code)

One **hub** (`chase.py`) + a **visual Command Center** (`command_center.py`) over a set
of standalone tools that share a Supabase warehouse. Everything is plain Python stdlib
+ pandas/requests — no framework to learn.

## Run it
```
cd C:\Users\chase\Documents\bet-evaluator
python command_center.py        # or: python chase.py serve  (or double-click Command-Center.bat)
# open http://localhost:8787
```
Every CLI tool: `python chase.py <cmd>` (value | game AWAY@HOME | edge | cross | book |
scenarios | regress | model | report | sharp | predict | bet | serve).

## The map (what each file is)
| File | Role |
|------|------|
| `bet_evaluator.py` | expected-runs model (probabilities, value) |
| `game_report.py` | per-game ML / Total / F5 / Pitcher-Ks (live Kalshi lines) |
| `regression.py` | pitcher regression/progression (FIP-ERA luck gap, workload) |
| `backtest/value_board.py` | today's ranked value board |
| `backtest/market_edge.py` | profit scan (ROI/Kelly/bootstrap/FDR) |
| `backtest/cross_venue.py` | Kalshi vs commercial books: arb/value/thin |
| `backtest/book_intel.py` | which book is sharp vs soft |
| `backtest/sharp_*` / `analyze_*` | sharp tracking + analysis |
| `backtest/prediction_markets.py` | Kalshi ingestion (ml, F5, K markets) |
| `command_center.py` | the visual app (wraps the above, reads Supabase) |
| `chase.py` | CLI hub that dispatches to all of them |

## How to ADD a tool to the Command Center
1. Write the tool with a `run()` that prints (or a `compute()` that returns data).
2. In `command_center.py`: add a `def x_text(): from backtest import x; return _capture(x.run)`,
   add it to the `ROUTES` dict, and add a `<button class=navbtn onclick="load('x',this)">`
   in `PAGE`.
3. That's it — the UI captures the output and renders it.

## How to ADD a market/data source
Pattern is in `prediction_markets.py` (`f5_market`, `ks_market`): a small fetcher that
hits a free API and returns a dict; then consume it in `game_report.py`.

## Data layer (Supabase)
`backtest/db.py` is a tiny PostgREST client (`select/insert/upsert/count`). Schema in
`backtest/schema.sql` (idempotent — `python -m apply_schema` after edits). The vault
`07-Workflows/Sharp-Tools-How-To` and `06-Betting-Logic/Market-Edge-Engine` document the
logic.

## Continue with an AI agent
Point any coding agent at this repo + `DEVELOPMENT.md`. The tools are small, single-
purpose, and stdout-based, so they're easy to extend or wrap in a richer UI later
(Flask/React) without rewriting the logic.
