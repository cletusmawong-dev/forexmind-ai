-- ForexMind trading accounts (phase 3).
-- Run ONCE in the Supabase SQL editor. Do not re-run the older
-- supabase_schema.sql. This file only adds the accounts table.
--
-- After it succeeds, restart the API container (deploy/vps), not the
-- MT5 bridge on port 8700. The next API boot inserts slot 1 for the
-- owner when MT5_BRIDGE_URL is set and the owner has no account row yet.
-- Until that row exists, orders keep using the single bridge as they do today.

create table if not exists trading_accounts (
  id text primary key,
  data jsonb not null
);

create index if not exists idx_trading_accounts_user
  on trading_accounts ((data->>'userId'));

-- Optional combined gold cap (off when this row is absent).
-- insert into settings (id, data) values (
--   'book_risk',
--   '{"combined_gold_cap_usd": null}'::jsonb
-- ) on conflict (id) do nothing;
