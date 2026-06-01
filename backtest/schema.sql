-- ============================================================================
-- MLBMA Betting Data + Outcomes Backtest — Supabase (Postgres) schema
-- Phase 1. The historical truth layer: pre-game metric snapshots, market
-- snapshots, and final outcomes, joined ONLY where the snapshot existed before
-- first pitch (no look-ahead bias).
--
-- Apply with:  psql "$SUPABASE_DB_URL" -f backtest/schema.sql
--          or paste into the Supabase SQL editor.
-- ============================================================================

-- ── Reference ────────────────────────────────────────────────────────────────
create table if not exists teams (
  team_id     int primary key,
  team_abbr   text unique not null,
  team_name   text,
  league      text,
  division    text
);

create table if not exists games (
  game_pk         bigint primary key,
  season          int,
  game_date       date,
  scheduled_start timestamptz,          -- the look-ahead cutoff for this game
  home_team       text references teams(team_abbr),
  away_team       text references teams(team_abbr),
  venue           text,
  status          text,                 -- scheduled / final / postponed
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);
create index if not exists idx_games_date on games(game_date);
create index if not exists idx_games_start on games(scheduled_start);

create table if not exists game_outcomes (
  game_pk                  bigint primary key references games(game_pk),
  home_runs                int,
  away_runs                int,
  home_f5_runs             int,
  away_f5_runs             int,
  total_runs               int,
  margin_home              int,
  winner_team              text references teams(team_abbr),
  home_starter_id          bigint,
  away_starter_id          bigint,
  home_starter_ip          numeric,
  away_starter_ip          numeric,
  home_starter_er          int,
  away_starter_er          int,
  home_quality_start       boolean,
  away_quality_start       boolean,
  save_pitcher_id          bigint,
  blown_save_home          boolean,
  blown_save_away          boolean,
  home_bullpen_runs_allowed int,
  away_bullpen_runs_allowed int,
  ingested_at              timestamptz default now()
);

-- ── Versioning (referenced by snapshots/predictions) ─────────────────────────
create table if not exists metric_versions (
  metric_version      text primary key,
  created_at          timestamptz default now(),
  abq_formula_json    jsonb,
  rcv_formula_json    jsonb,
  obr_formula_json    jsonb,
  osi_formula_json    jsonb,
  pitch_score_formula_json jsonb,
  notes               text
);

create table if not exists model_versions (
  model_version    text primary key,
  created_at       timestamptz default now(),
  model_type       text,
  feature_set_json jsonb,
  calibration_method text,
  notes            text
);

-- ── Metric snapshots (point-in-time, pre-game) ───────────────────────────────
create table if not exists team_metric_snapshots (
  snapshot_id          bigint generated always as identity primary key,
  game_pk              bigint references games(game_pk),
  team                 text references teams(team_abbr),
  opponent             text references teams(team_abbr),
  snapshot_time        timestamptz not null,
  lineup_status        text,            -- projected / confirmed / final
  split_used           text,            -- vs_RHP / vs_LHP / blended
  window_used          text,            -- YTD / L30 / L14 / L7
  is_home              boolean,
  opposing_starter_id  bigint,
  opposing_starter_hand text,
  abq numeric, rcv numeric, obr numeric, osi numeric, proj_osi numeric,
  reg_signal numeric, pals numeric, oor numeric, pp_gap numeric, df_gap numeric,
  wrc_plus numeric, woba numeric, xwoba numeric, slg numeric,
  k_pct numeric, bb_pct numeric, barrel_pct numeric, hardhit_pct numeric,
  chase_pct numeric, swstr_pct numeric, zcon_pct numeric, ocon_pct numeric,
  metric_version       text references metric_versions(metric_version),
  pipeline_run_id      text
);
create index if not exists idx_tms_game on team_metric_snapshots(game_pk);
create index if not exists idx_tms_time on team_metric_snapshots(snapshot_time);

create table if not exists pitcher_metric_snapshots (
  snapshot_id    bigint generated always as identity primary key,
  game_pk        bigint references games(game_pk),
  pitcher_id     bigint,
  pitcher_name   text,
  team           text references teams(team_abbr),
  snapshot_time  timestamptz not null,
  hand           text,
  role           text,            -- starter / reliever
  era numeric, fip numeric, xfip numeric, k_pct numeric, bb_pct numeric, hr9 numeric,
  pitch_score numeric, f5_era numeric,
  osi_allowed numeric, abq_allowed numeric, rcv_allowed numeric, obr_allowed numeric,
  avg_ip numeric, l14_starts int,
  stale_flag boolean default false,
  staleness_warning text,
  metric_version text references metric_versions(metric_version),
  pipeline_run_id text
);
create index if not exists idx_pms_game on pitcher_metric_snapshots(game_pk);
create index if not exists idx_pms_time on pitcher_metric_snapshots(snapshot_time);

create table if not exists bullpen_metric_snapshots (
  snapshot_id    bigint generated always as identity primary key,
  game_pk        bigint references games(game_pk),
  team           text references teams(team_abbr),
  snapshot_time  timestamptz not null,
  overall_era numeric, overall_fip numeric, overall_whip numeric,
  overall_k_pct numeric, overall_bb_pct numeric, overall_hr9 numeric,
  inherited_scored_pct numeric,
  osi_allowed numeric, abq_allowed numeric, rcv_allowed numeric, obr_allowed numeric,
  high_leverage_era numeric, high_leverage_fip numeric, high_leverage_whip numeric,
  metric_version text references metric_versions(metric_version),
  pipeline_run_id text
);
create index if not exists idx_bms_game on bullpen_metric_snapshots(game_pk);
create index if not exists idx_bms_time on bullpen_metric_snapshots(snapshot_time);

-- ── Betting market storage ────────────────────────────────────────────────────
create table if not exists odds_snapshots (
  odds_snapshot_id     bigint generated always as identity primary key,
  game_pk              bigint references games(game_pk),
  snapshot_time        timestamptz not null,
  sportsbook           text,
  market_type          text,   -- ml / runline / total / team_total / f5_ml / f5_total / prop
  period               text,   -- full_game / first_five / player
  selection            text,   -- LAD / over / under / player_over
  line                 numeric,
  american_odds        int,
  decimal_odds         numeric,
  implied_probability  numeric,
  is_best_price        boolean,
  source               text
);
create index if not exists idx_odds_game on odds_snapshots(game_pk);
create index if not exists idx_odds_time on odds_snapshots(snapshot_time);
create index if not exists idx_odds_lookup on odds_snapshots(game_pk, market_type, selection);

create table if not exists market_closing_lines (
  game_pk                     bigint references games(game_pk),
  market_type                 text,
  selection                   text,
  line                        numeric,
  closing_odds                int,
  closing_implied_probability numeric,
  closing_snapshot_time       timestamptz,
  primary key (game_pk, market_type, selection)
);

-- ── Model & bet evaluation ────────────────────────────────────────────────────
create table if not exists model_predictions (
  prediction_id              bigint generated always as identity primary key,
  game_pk                    bigint references games(game_pk),
  prediction_time            timestamptz not null,
  market_type                text,
  selection                  text,
  line                       numeric,
  model_version              text references model_versions(model_version),
  metric_version             text references metric_versions(metric_version),
  model_probability          numeric,
  market_implied_probability numeric,
  no_vig_probability         numeric,
  edge                       numeric,
  expected_value             numeric,
  fair_odds                  int,
  projected_home_runs        numeric,
  projected_away_runs        numeric,
  projected_total            numeric,
  projected_margin           numeric,
  features_json              jsonb,
  verdict                    text     -- play / pass / review
);
create index if not exists idx_pred_game on model_predictions(game_pk);
create index if not exists idx_pred_time on model_predictions(prediction_time);

create table if not exists bet_logs (
  bet_id           bigint generated always as identity primary key,
  prediction_id    bigint references model_predictions(prediction_id),
  date             date,
  game_pk          bigint references games(game_pk),
  market_type      text,
  pick             text,
  line             numeric,
  odds_bet         int,
  sportsbook       text,
  stake_units      numeric,
  confidence_tier  text,
  result           text,    -- win / loss / push / void
  profit_units     numeric,
  closing_odds     int,
  clv              numeric,
  note_path        text
);
create index if not exists idx_bet_game on bet_logs(game_pk);

-- ── Sharp money / line movement signals ──────────────────────────────────────
create table if not exists sharp_signals (
  sharp_signal_id  bigint generated always as identity primary key,
  game_pk          bigint references games(game_pk),
  snapshot_time    timestamptz not null,
  market_type      text,
  selection        text,           -- the side sharp money favors
  line             numeric,
  sharp_novig_prob numeric,        -- de-vigged consensus across sharp books
  soft_novig_prob  numeric,        -- de-vigged consensus across soft books
  divergence       numeric,        -- sharp - soft (positive = sharps like this side)
  n_sharp_books    int,
  n_soft_books     int,
  line_open        int,            -- consensus American at first snapshot
  line_current     int,            -- consensus American now
  line_delta       int,            -- movement toward this side
  steam_flag       boolean,        -- multi-book simultaneous move
  steam_books      int,
  sharp_books_used text,
  source           text default 'the-odds-api'
);
create index if not exists idx_sharp_game on sharp_signals(game_pk);
create index if not exists idx_sharp_time on sharp_signals(snapshot_time);

-- ── Per-book sharp observations (the "which book to respect" record) ─────────
-- One row per sharp book that diverges from the soft consensus, with the time +
-- conditions it appeared under, graded against the outcome after the game.
create table if not exists sharp_observations (
  obs_id           bigint generated always as identity primary key,
  game_pk          bigint references games(game_pk),
  snapshot_time    timestamptz not null,
  minutes_to_fp    int,            -- minutes before scheduled_start (NULL if after)
  time_bucket      text,           -- early / midday / pregame / close
  book             text,           -- the sharp book
  market_type      text,
  selection        text,
  line             numeric,
  book_novig_prob  numeric,        -- this book's de-vigged prob for the side
  soft_novig_prob  numeric,        -- soft consensus de-vigged prob
  divergence       numeric,        -- book - soft (positive = book likes this side)
  side_role        text,           -- fav / dog / over / under
  home_away        text,           -- home / away / na
  settled          boolean default false,
  won              boolean,        -- did the sharp side win/cover
  push             boolean,
  closing_soft_novig numeric,      -- soft consensus at last pre-game snapshot
  market_moved_to_sharp boolean,   -- did soft consensus drift toward the sharp side (CLV proxy)
  metric_version   text,
  source           text default 'the-odds-api'
);
create index if not exists idx_obs_game on sharp_observations(game_pk);
create index if not exists idx_obs_book on sharp_observations(book);
create index if not exists idx_obs_settled on sharp_observations(settled);

-- ============================================================================
-- Look-ahead-safe views: ONLY surface snapshots taken before first pitch.
-- All backtests must query through these, never the raw tables directly.
-- ============================================================================
create or replace view v_team_snapshots_pregame as
  select s.* from team_metric_snapshots s
  join games g on g.game_pk = s.game_pk
  where g.scheduled_start is not null and s.snapshot_time < g.scheduled_start;

-- Past vs future separation (analyze both, kept distinct).
create or replace view v_games_past as
  select * from games where status = 'final';
create or replace view v_games_upcoming as
  select * from games where status is distinct from 'final';

-- Past outcome base rates (the historical truth, outcome-based).
create or replace view v_outcome_base_rates as
  select count(*) as games,
         round(avg(case when o.winner_team = g.home_team then 1.0 else 0.0 end), 4) as home_win_rate,
         round(avg(o.total_runs), 2) as avg_total_runs,
         round(avg(o.margin_home), 2) as avg_margin_home,
         round(avg(o.home_runs), 2) as avg_home_runs,
         round(avg(o.away_runs), 2) as avg_away_runs,
         round(stddev_pop(o.total_runs), 2) as sd_total_runs
  from game_outcomes o
  join games g on g.game_pk = o.game_pk
  where g.status = 'final';

-- Per-team historical record (home & away win rate).
create or replace view v_team_outcome_perf as
  select t.team_abbr as team,
         count(*) filter (where g.home_team = t.team_abbr) as home_g,
         round(avg(case when g.home_team = t.team_abbr and o.winner_team = t.team_abbr then 1.0
                        when g.home_team = t.team_abbr then 0.0 end), 4) as home_win_rate,
         count(*) filter (where g.away_team = t.team_abbr) as away_g,
         round(avg(case when g.away_team = t.team_abbr and o.winner_team = t.team_abbr then 1.0
                        when g.away_team = t.team_abbr then 0.0 end), 4) as away_win_rate
  from teams t
  join games g on (g.home_team = t.team_abbr or g.away_team = t.team_abbr) and g.status = 'final'
  join game_outcomes o on o.game_pk = g.game_pk
  group by t.team_abbr;

-- Sharp performance — the cross-reference: which book/market/time/condition wins.
create or replace view v_sharp_performance as
  select book, market_type, time_bucket, side_role,
         count(*) as n,
         sum(case when won then 1 else 0 end) as wins,
         round(avg(case when won then 1.0 else 0.0 end), 4) as win_rate,
         round(avg(divergence), 4) as avg_divergence
  from sharp_observations
  where settled and won is not null and not coalesce(push, false)
  group by book, market_type, time_bucket, side_role;

create or replace view v_sharp_book_performance as
  select book,
         count(*) as n,
         sum(case when won then 1 else 0 end) as wins,
         round(avg(case when won then 1.0 else 0.0 end), 4) as win_rate,
         round(avg(divergence), 4) as avg_divergence
  from sharp_observations
  where settled and won is not null and not coalesce(push, false)
  group by book
  order by win_rate desc;

create or replace view v_odds_pregame as
  select o.* from odds_snapshots o
  join games g on g.game_pk = o.game_pk
  where g.scheduled_start is not null and o.snapshot_time < g.scheduled_start;

-- Latest pre-game team snapshot per (game, team) joined to the final outcome.
create or replace view v_backtest_team_base as
  select distinct on (s.game_pk, s.team)
    s.*, go.home_runs, go.away_runs, go.total_runs, go.margin_home,
    go.winner_team, go.home_f5_runs, go.away_f5_runs
  from v_team_snapshots_pregame s
  join game_outcomes go on go.game_pk = s.game_pk
  order by s.game_pk, s.team, s.snapshot_time desc;

-- ============================================================================
-- Phase A — evaluator prediction logging + settlement loop
-- ============================================================================

-- Settlement columns on model_predictions (idempotent; table predates Phase A).
-- CLV proxy for a prediction = closing market-implied prob for the side minus the
-- market-implied prob at prediction time (positive = market moved toward our side).
alter table model_predictions add column if not exists settled boolean default false;
alter table model_predictions add column if not exists won boolean;
alter table model_predictions add column if not exists push boolean;
alter table model_predictions add column if not exists side_role text;       -- fav/dog/over/under
alter table model_predictions add column if not exists closing_implied_probability numeric;
alter table model_predictions add column if not exists clv numeric;
alter table model_predictions add column if not exists settled_at timestamptz;
create index if not exists idx_pred_settled on model_predictions(settled);

-- Predictions made before first pitch (look-ahead-safe). Predictions are
-- inherently pre-game, but enforce the cutoff so a replayed/late eval can't leak.
create or replace view v_predictions_pregame as
  select p.* from model_predictions p
  join games g on g.game_pk = p.game_pk
  where g.scheduled_start is null or p.prediction_time < g.scheduled_start;

-- Closing line per (game, market, selection): the BEST price at the latest odds
-- snapshot strictly before first pitch. Derived from odds_snapshots so CLV works
-- without a separate closing-line capture job (market_closing_lines stays optional).
create or replace view v_closing_lines as
  select distinct on (o.game_pk, o.market_type, o.selection)
    o.game_pk, o.market_type, o.selection, o.line,
    o.american_odds       as closing_odds,
    o.decimal_odds        as closing_decimal,
    o.implied_probability as closing_implied_probability,
    o.snapshot_time       as closing_snapshot_time
  from odds_snapshots o
  join games g on g.game_pk = o.game_pk
  where g.scheduled_start is not null and o.snapshot_time < g.scheduled_start
  order by o.game_pk, o.market_type, o.selection, o.snapshot_time desc, o.decimal_odds desc;

-- Settled predictions joined to the final outcome (the grading base for calibration).
create or replace view v_prediction_results as
  select p.*, go.winner_team, go.total_runs, go.margin_home,
         go.home_runs, go.away_runs, go.home_f5_runs, go.away_f5_runs
  from model_predictions p
  join game_outcomes go on go.game_pk = p.game_pk;

-- Placed bets joined to outcomes (ROI / record base).
create or replace view v_bet_results as
  select b.*, go.winner_team, go.total_runs, go.margin_home,
         go.home_runs, go.away_runs
  from bet_logs b
  join game_outcomes go on go.game_pk = b.game_pk;

-- Daily report / contradiction monitor output (written by backtest.daily_report,
-- read+posted by the chase-discord-bot; also mirrored to the vault 15-Reports/).
create table if not exists daily_reports (
  report_id        bigint generated always as identity primary key,
  report_date      date not null,
  generated_at     timestamptz default now(),
  headline         text,
  n_contradictions int default 0,
  contradictions   jsonb,     -- [{kind, market, detail, suggestion}]
  flags            jsonb,     -- sustainability / CLV flags
  summary_md       text,      -- full markdown body (vault note + bot embed source)
  posted           boolean default false   -- bot flips true after posting
);
create index if not exists idx_daily_reports_date on daily_reports(report_date desc);

-- ============================================================================
-- Phase B — calibration / ROI / CLV / sustainability expressors
-- (ROI uses the evaluated price recovered as decimal = 1 / market-implied prob,
--  so no separate odds column is needed.)
-- ============================================================================

-- Calibration: predicted-probability bucket (5% wide) vs actual win rate.
create or replace view v_calibration_buckets as
  select width_bucket(model_probability, 0, 1, 20) as prob_bucket,
         count(*)                                            as n,
         round(avg(model_probability)::numeric, 4)          as avg_predicted,
         round(avg(case when won then 1.0 else 0.0 end), 4)  as actual_win_rate,
         round((avg(model_probability) - avg(case when won then 1.0 else 0.0 end))::numeric, 4)
                                                             as overconfidence
  from model_predictions
  where settled and won is not null and not coalesce(push, false)
  group by 1 order by 1;

-- ROI + hit rate by edge bucket (2.5pt) x market, at the evaluated price.
create or replace view v_roi_by_edge_tier as
  select market_type,
         (floor(edge / 0.025) * 0.025)                       as edge_bucket,
         count(*)                                            as n,
         sum(case when won then 1 else 0 end)                as wins,
         round(avg(case when won then 1.0 else 0.0 end), 4)  as win_rate,
         round(avg(model_probability)::numeric, 4)           as avg_model_prob,
         round(avg(market_implied_probability)::numeric, 4)  as avg_implied,
         round(avg(case when won then (1.0/nullif(market_implied_probability,0) - 1.0)
                        else -1.0 end)::numeric, 4)          as roi_per_unit
  from model_predictions
  where settled and won is not null and not coalesce(push, false)
    and market_implied_probability is not null
  group by 1, 2 order by 1, 2;

-- CLV beat-rate (leading indicator) by market.
create or replace view v_clv_beat_rate as
  select market_type,
         count(*) filter (where clv is not null)                            as n,
         round(avg(case when clv > 0 then 1.0 else 0.0 end)
               filter (where clv is not null), 4)                           as clv_beat_rate,
         round(avg(clv) filter (where clv is not null)::numeric, 4)         as avg_clv
  from model_predictions
  where settled
  group by market_type order by market_type;

-- Sustainability: per (market, edge bucket) — is the edge repeatable (win_rate,
-- ROI, CLV positive with adequate sample) or variance? overconfidence drives the
-- "shade the model" recommendation in the daily report.
create or replace view v_edge_sustainability as
  select market_type,
         (floor(edge / 0.025) * 0.025)                       as edge_bucket,
         count(*)                                            as n,
         round(avg(case when won then 1.0 else 0.0 end), 4)  as win_rate,
         round(avg(model_probability)::numeric, 4)           as avg_model_prob,
         round((avg(model_probability) - avg(case when won then 1.0 else 0.0 end))::numeric, 4)
                                                             as overconfidence,
         round(avg(case when won then (1.0/nullif(market_implied_probability,0) - 1.0)
                        else -1.0 end)::numeric, 4)          as roi_per_unit,
         round(avg(clv)::numeric, 4)                         as avg_clv
  from model_predictions
  where settled and won is not null and not coalesce(push, false)
  group by 1, 2 order by 1, 2;
