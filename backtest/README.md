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
3. `pip install psycopg2-binary`
4. Apply the schema (idempotent — re-run whenever it grows):
   ```
   python -m backtest.apply_schema
   ```
   (uses `SUPABASE_DB_URL`; or paste `schema.sql` into the Supabase SQL editor)

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
- [x] **Phase 1** — schema + look-ahead-safe views + config wiring
- [x] **Phase 2** — CSV → snapshot importers (team/pitcher/bullpen/games)
- [x] **Phase 3** — odds snapshot ingestion + closing-line capture (`v_closing_lines`)
- [x] **Phase A** — evaluator prediction logging + settlement + CLV
- [x] **Phase B** — calibration / ROI / CLV / sustainability expressors
- [x] **Phase B-Report** — daily report + contradiction monitor (Discord + vault)
- [ ] **Phase B-BI** — Metabase dashboards (compose + role provided; needs Docker)
- [ ] **Phase C** — sharp softness / avoid-segment expressors
- [ ] **Phase D** — metric-target validation + weight-update recommendations
- [ ] **Phase E** — unified `v_game_features` hub for intelligent modules
- [ ] **Phase F** — vault write-back + daily orchestrator

## Evaluator learning loop (Phase A/B)
```
python bet_evaluator.py --game "TOR@BAL" --market ml --side BAL   # logs model_predictions
python -m backtest.import_outcomes        # after games go final
python -m backtest.settle_predictions     # grade won/push + CLV proxy
python -m backtest.analyze_model          # calibration / ROI / CLV (--min-n gated)
python -m backtest.daily_report           # contradiction monitor -> vault + Supabase
```
- `bet_evaluator.py` logs every evaluation to `model_predictions` (skip with `--no-log`).
- CLV proxy = closing market-implied prob (from `v_closing_lines`, derived from
  `odds_snapshots`) minus the implied prob at prediction time.
- `daily_report` flags where the layers disagree (model over/under-confidence vs
  calibrated history, unprofitable model edges, CLV loss) and writes recommend-only
  congruence suggestions to `15-Reports/<date>.md` + the `daily_reports` table.
  The **chase-discord-bot** reads that table and posts it in the postgame recap.

## BI dashboards (Metabase)
1. `docker compose -f docker-compose.metabase.yml up -d` → http://localhost:3000
2. Create the read-only role: run `backtest/metabase_setup.sql` in the Supabase SQL
   editor (set a real password first).
3. In Metabase, Add Database → PostgreSQL with the Supabase host/port/db and the
   `metabase_ro` role. Seed charts on these views:
   - `v_calibration_buckets` — predicted vs actual (calibration curve)
   - `v_roi_by_edge_tier` · `v_edge_sustainability` — ROI / sustainability by edge
   - `v_clv_beat_rate` — CLV beat-rate over time
   - `v_sharp_book_performance` · `v_sharp_performance` — sharp track record
   - `daily_reports` — the contradiction-monitor history

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
