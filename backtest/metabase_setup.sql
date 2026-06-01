-- ============================================================================
-- Read-only BI role for Metabase / Grafana on the betting warehouse.
-- Run ONCE in the Supabase SQL editor (or psql "$SUPABASE_DB_URL" -f this file).
-- Replace 'CHANGE_ME' with a strong password; NEVER commit the real password.
--
-- The BI tool connects with this role and can only SELECT — it cannot write,
-- so dashboards can never mutate the truth layer.
-- ============================================================================

do $$
begin
  if not exists (select from pg_roles where rolname = 'metabase_ro') then
    create role metabase_ro login password 'CHANGE_ME';
  end if;
end $$;

grant connect on database postgres to metabase_ro;
grant usage on schema public to metabase_ro;

-- Existing tables + views (views are included by "all tables").
grant select on all tables in schema public to metabase_ro;

-- Future tables/views automatically readable (so new expressors just appear).
alter default privileges in schema public grant select on tables to metabase_ro;

-- Optional: restrict to just the analytical surface instead of everything, e.g.
--   revoke select on all tables in schema public from metabase_ro;
--   grant select on
--     v_calibration_buckets, v_roi_by_edge_tier, v_clv_beat_rate,
--     v_edge_sustainability, v_sharp_book_performance, v_sharp_performance,
--     v_prediction_results, daily_reports
--   to metabase_ro;
