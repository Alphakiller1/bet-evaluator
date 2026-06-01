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

-- ============================================================================
-- Look-ahead-safe views: ONLY surface snapshots taken before first pitch.
-- All backtests must query through these, never the raw tables directly.
-- ============================================================================
create or replace view v_team_snapshots_pregame as
  select s.* from team_metric_snapshots s
  join games g on g.game_pk = s.game_pk
  where g.scheduled_start is not null and s.snapshot_time < g.scheduled_start;

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
