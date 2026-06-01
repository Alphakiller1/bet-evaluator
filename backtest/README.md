# MLBMA Backtest — Supabase historical truth layer

The missing loop:
**metrics → pre-game prediction → market price → outcome → CLV/result → calibration → better metrics.**

- `mlbma_pipeline` stays the metric factory (read-only source).
- `chase-analytics-brain` stays the qualitative/research memory.
- **Supabase** is the historical warehouse: point-in-time snapshots + outcomes.

## The non-negotiable rule
Every prediction is tied to **what was known before first pitch**. All backtests
query through the look-ahead-safe views (`v_*_pregame`, `v_backtest_team_base`),
which enforce `snapshot_time < games.scheduled_start`. Never backtest against
refreshed same-day/current-season metrics.

## Honest constraint (read this first)
There is **no historical archive** of pre-game metric or odds snapshots — the
pipeline only holds today's refreshed numbers, and the free odds tier is
current-only. So:
- **Past metrics cannot be backtested without look-ahead bias.** We only have past
  *outcomes* (`game_results.csv`).
- This system is **prospective**: it starts banking truth the day we deploy it.
  Real metric calibration comes after weeks/months of captured games.
- Backfilling history later requires a paid historical-odds source + reconstructed
  point-in-time metrics; treat that as a separate, optional effort.

## Setup (one-time)
1. Create a free project at https://supabase.com.
2. Add to `../.env` (gitignored):
   ```
   SUPABASE_URL=https://<ref>.supabase.co
   SUPABASE_KEY=<service-role key>
   SUPABASE_DB_URL=postgresql://postgres:<pw>@<host>:5432/postgres
   ```
3. Apply the schema:
   ```
   psql "$SUPABASE_DB_URL" -f backtest/schema.sql
   ```
   (or paste `schema.sql` into the Supabase SQL editor)
4. `pip install supabase psycopg2-binary`

## Data flow
1. Run `mlbma_pipeline` (produces CSVs).
2. `python -m backtest.import_snapshots`  → push current metric snapshots
   (team / pitcher / bullpen) with `snapshot_time = now`, tagged `metric_version`.
3. `python market_data.py --fetch` at open / midday / lineup / ~30m pregame / close
   → `import_odds` lands timestamped market snapshots.
4. After games finish: `python -m backtest.import_outcomes` from `game_results.csv`.
5. `python -m backtest.settle` settles every market and computes CLV.
6. `python -m backtest.run_backtest` joins pre-game snapshot + pre-game odds +
   outcome through the safe views.

## Schema (`schema.sql`)
teams · games · game_outcomes · team/pitcher/bullpen_metric_snapshots ·
odds_snapshots · market_closing_lines · model_predictions · bet_logs ·
metric_versions · model_versions · look-ahead-safe views.

## Build phases
- [x] **Phase 1** — schema + look-ahead-safe views + config wiring  ← *here*
- [ ] **Phase 2** — CSV → snapshot importers (team/pitcher/bullpen/games)
- [ ] **Phase 3** — odds snapshot ingestion + closing-line capture
- [ ] **Phase 4** — outcome settlement + CLV
- [ ] **Phase 5** — backtest engine (Brier, log-loss, calibration, ROI by edge/tier,
      CLV; run MAE/RMSE/bias; signal hit-rate/ROI/CLV)
- [ ] **Phase 6** — metric-target validation + model-version comparison
      (market-only vs MLBMA-only vs hybrid, judged against closing lines)

## Metric backtest targets (what each metric is actually judged on)
ABQ → opp starter pitches, pitches/PA, lower lineup K%, higher BB%, lower Chase%/
SwStr%, higher contact, starter early exits, opp QS rate, bullpen exposure.
RCV → team runs, HR/XBH, barrels, team-total overs, slug spikes.
OBR → on-base floor, walk rate, run-scoring stability, avoiding dead games.
OSI/projOSI → expected runs, ML edge, team-total edge, total contribution, split edge.
PitchScore → run prevention, FIP/ERA, K-BB, HR suppression, game score, F5 runs.
Bullpen → 6th–9th runs, blown saves, inherited scored, late-game margin protection.

The real question isn't "can MLBMA predict winners" — it's **"can MLBMA identify when
the market is mispriced?"** So the hybrid model is judged against **closing lines**.
