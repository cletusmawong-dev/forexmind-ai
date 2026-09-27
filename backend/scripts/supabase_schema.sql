-- ForexMind AI - Supabase schema (§21.9 migration PREP)
-- One table per collection: id (the doc id) + data (the whole doc as JSONB).
-- Run ONCE in the Supabase SQL editor BEFORE the backfill. RLS is disabled
-- because access goes through the service key server-side ONLY.
--
-- Collections (must match backend/app/db/store.py COLLECTIONS):
create table if not exists users (id text primary key, data jsonb not null);
create table if not exists markets (id text primary key, data jsonb not null);
create table if not exists signals (id text primary key, data jsonb not null);
create table if not exists trades (id text primary key, data jsonb not null);
create table if not exists strategies (id text primary key, data jsonb not null);
create table if not exists strategy_versions (id text primary key, data jsonb not null);
create table if not exists agent_goals (id text primary key, data jsonb not null);
create table if not exists agent_activity (id text primary key, data jsonb not null);
create table if not exists lessons (id text primary key, data jsonb not null);
create table if not exists hypotheses (id text primary key, data jsonb not null);
create table if not exists experiments (id text primary key, data jsonb not null);
create table if not exists notifications (id text primary key, data jsonb not null);
create table if not exists performance (id text primary key, data jsonb not null);
create table if not exists settings (id text primary key, data jsonb not null);
create table if not exists backtests (id text primary key, data jsonb not null);
create table if not exists candles (id text primary key, data jsonb not null);
create table if not exists exec_commands (id text primary key, data jsonb not null);
create table if not exists market_regimes (id text primary key, data jsonb not null);
create table if not exists version_events (id text primary key, data jsonb not null);
create table if not exists research_hypotheses (id text primary key, data jsonb not null);
create table if not exists ai_decisions (id text primary key, data jsonb not null);
create table if not exists strategy2_state (id text primary key, data jsonb not null);
create table if not exists autopsies (id text primary key, data jsonb not null);
create table if not exists audit_log (id text primary key, data jsonb not null);
create table if not exists ai_memory (id text primary key, data jsonb not null);
create table if not exists exec_events (id text primary key, data jsonb not null);
create table if not exists regime_events (id text primary key, data jsonb not null);
create table if not exists recommendations (id text primary key, data jsonb not null);
create table if not exists tg_link_tokens (id text primary key, data jsonb not null);

-- Query performance for the common patterns (data->>col filters + order):
create index if not exists idx_signals_user_created on signals ((data->>'userId'), (data->>'createdAt') desc);
create index if not exists idx_events_user_created on exec_events ((data->>'userId'), (data->>'createdAt') desc);
create index if not exists idx_decisions_user_created on ai_decisions ((data->>'userId'), (data->>'createdAt') desc);
create index if not exists idx_audit_created on audit_log ((data->>'createdAt') desc);
create index if not exists idx_recos_user_status on recommendations ((data->>'userId'), (data->>'status'));
