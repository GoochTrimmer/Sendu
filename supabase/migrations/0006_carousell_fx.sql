-- Carousell (one marketplace per country) + live exchange rates.

insert into marketplaces (code, name) values
  ('carousell_ph', 'Carousell Philippines'),
  ('carousell_my', 'Carousell Malaysia'),
  ('carousell_id', 'Carousell Indonesia'),
  ('carousell_sg', 'Carousell Singapore'),
  ('carousell_hk', 'Carousell Hong Kong')
on conflict (code) do nothing;

-- Daily market rates (European Central Bank reference rates via frankfurter.dev),
-- refreshed by the worker at most every 12 hours. 1 unit of `currency` = rate_to_sgd SGD.
-- Used for price filters, Telegram alerts and the "≈ S$" shown next to local prices.
create table if not exists fx_rates (
  currency text primary key,
  rate_to_sgd numeric not null check (rate_to_sgd > 0),
  updated_at timestamptz not null default now()
);

alter table fx_rates enable row level security;
grant select on fx_rates to authenticated;
grant all on fx_rates to service_role;
drop policy if exists fx_rates_read on fx_rates;
create policy fx_rates_read on fx_rates for select to authenticated using (true);
