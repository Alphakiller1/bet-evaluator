# Bet Evaluator

Turns a single MLB bet into a structured analysis: a statistical probability plus
a worded read on implications, risk, and variance — then logs it into the
ChaseAnalytics-Brain vault.

## Boundaries
- **Reads** MLB data from `mlbma_pipeline/data/*.csv` (read-only — never writes there).
- **Writes** analyses into the vault: `ChaseAnalytics-Brain/13-Bet-History/`.
- Self-contained: no dependency on the `mlbma_pipeline` Python package.

## Install
```
pip install pandas
```

## Usage
```
python bet_evaluator.py --game "TOR@BAL"  --market ml         --side BAL          --odds -130
python bet_evaluator.py --game "ATL@CIN"  --market total      --side over --line 9.5  --odds -110
python bet_evaluator.py --game "NYY@ATH"  --market team_total --side NYY  --ou over --line 4.5 --odds +100
python bet_evaluator.py --game "PHI@LAD"  --market runline    --side LAD  --line -1.5 --odds +105
```
Add `--no-write` to print the summary without creating a vault note.

| Flag | Meaning |
|------|---------|
| `--game` | `AWAY@HOME` using the pipeline's team abbreviations (must be on today's slate) |
| `--market` | `ml` · `total` · `team_total` · `runline` |
| `--side` | team abbr / `home` / `away` (ml, runline, team_total); `over` / `under` (total) |
| `--ou` | `over` / `under` for `team_total` |
| `--line` | the number for total / team_total / runline |
| `--odds` | American odds, e.g. `-130`, `+145` |

## How the probability is built
Expected-runs model: `league_runs × offense_factor(OSI) × pitch_factor(opp SP FIP) × park`,
both factors regressed toward the mean (early-season inputs are noisy). Win prob comes
from a normal model on the expected run margin, blended with the empirical home base
rate. Totals/team-totals use a normal model on expected runs vs the posted line.

**This is a transparent heuristic, not a calibrated model.** It's a structured prior.
The vault bet-history + CLV log is how it gets calibrated over time. See the vault note
`06-Betting-Logic/Win-Probability-Model`.

## Market data (odds scraper)
`market_data.py` pulls the betting market — odds, line movement, best price across
books — from The Odds API (free tier). This is the data neither the vault nor the
pipeline had.

**On-demand, not scheduled.** Odds are fetched live **when you put in a bet**, for
just that game — no background polling. This keeps the free tier (~165 fetches/mo)
spent only on games you actually bet.

```
# one-time: get a free key at https://the-odds-api.com
$env:ODDS_API_KEY = "your_key"          # PowerShell  (or store in .env)
```
Omit `--odds` and the evaluator live-fetches this game's odds, uses the best price,
and stores a snapshot for movement/CLV:
```
python bet_evaluator.py --game "ARI@SEA" --market ml --side SEA
python bet_evaluator.py --game "ARI@SEA" --market ml --side SEA --no-fetch   # cached only
python bet_evaluator.py --game "ARI@SEA" --market total --side over --line 7.5 --props
```
Manual `--odds` skips the fetch entirely (0 credits). Direct scraper commands:
```
python market_data.py --fetch-game "ARI@SEA"   # one game, on demand
python market_data.py --fetch                  # whole slate (scan; ~3 credits)
python market_data.py --show "ARI@SEA"         # current prices + movement
```
Each fetch appends to `data/odds_history.csv` (line movement / closing line) and
merges into `data/odds_latest.csv`. Because fetches happen as you bet, movement
history naturally builds for the games you care about — evaluate again near first
pitch to capture the closing line for CLV.

## Config
Paths and model anchors live in `config.py`. Override data/vault locations with the
`MLBMA_DATA_DIR` and `CHASE_VAULT_ROOT` environment variables, and the odds key with
`ODDS_API_KEY`.
