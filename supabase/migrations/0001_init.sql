-- Sourcer: initial schema, RLS policies, triggers and helper functions.
-- Deviations from the README sketch (all additive):
--   * listings.previous_price / price_dropped_at  -> drives the "price drop" badge
--   * seen_listings.last_listed_at                -> needed to detect bumps
--   * search_state.last_error                     -> surfaced in the UI
--   * prune_storage() SQL function                -> called by `python -m sourcer prune`
--   * column-level grants so users can't set is_admin / telegram_chat_id themselves

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  telegram_chat_id bigint,
  telegram_link_code text unique,
  notify_mode text not null default 'instant' check (notify_mode in ('instant','digest','off')),
  is_admin boolean not null default false,
  created_at timestamptz not null default now()
);

create table marketplaces (
  code text primary key,
  name text not null,
  enabled boolean not null default true,
  paused_until timestamptz,
  consecutive_failures int not null default 0,
  last_error text,
  last_error_at timestamptz
);

create type search_priority as enum ('urgent','normal','daily');

create table saved_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  title text not null,
  query text not null,
  group_name text,
  marketplaces text[] not null,
  include_terms text[] not null default '{}',
  exclude_terms text[] not null default '{}',
  exclude_enabled boolean not null default true,
  spam_filter_enabled boolean not null default true,
  min_price numeric,
  max_price numeric,
  currency text not null default 'SGD',
  sizes text[] not null default '{}',
  priority search_priority not null default 'normal',
  is_paused boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table search_marketplace_overrides (
  search_id uuid references saved_searches(id) on delete cascade,
  marketplace text references marketplaces(code),
  query_override text,
  exclude_enabled boolean,
  extra_exclude_terms text[] not null default '{}',
  primary key (search_id, marketplace)
);

create table search_state (
  search_id uuid references saved_searches(id) on delete cascade,
  marketplace text references marketplaces(code),
  initialized boolean not null default false,
  last_polled_at timestamptz,
  last_new_count int,
  last_run_stats jsonb,
  consecutive_errors int not null default 0,
  last_error text,
  primary key (search_id, marketplace)
);

create table seen_listings (
  marketplace text not null,
  external_id text not null,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  last_price numeric,
  last_listed_at timestamptz,
  primary key (marketplace, external_id)
);

create table listings (
  id uuid primary key default gen_random_uuid(),
  marketplace text not null references marketplaces(code),
  external_id text not null,
  title text not null,
  description_snippet text check (char_length(description_snippet) <= 500),
  price numeric,
  previous_price numeric,
  price_dropped_at timestamptz,
  currency text,
  size text,
  brand text,
  seller_id text,
  seller_name text,
  url text not null,
  image_urls text[] not null default '{}' check (cardinality(image_urls) <= 4),
  listed_at timestamptz,
  status text not null default 'active' check (status in ('active','sold','removed')),
  is_bumped boolean not null default false,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (marketplace, external_id)
);

create table matches (
  id uuid primary key default gen_random_uuid(),
  search_id uuid not null references saved_searches(id) on delete cascade,
  listing_id uuid not null references listings(id) on delete cascade,
  user_id uuid not null references profiles(id) on delete cascade,
  matched_at timestamptz not null default now(),
  is_initial boolean not null default false,
  notified_at timestamptz,
  state text not null default 'new' check (state in ('new','seen','dismissed')),
  unique (search_id, listing_id)
);

create table listing_feedback (
  user_id uuid references profiles(id) on delete cascade,
  listing_id uuid references listings(id) on delete cascade,
  feedback text not null check (feedback in ('like','dislike')),
  created_at timestamptz not null default now(),
  primary key (user_id, listing_id)
);

create table price_history (
  id bigserial primary key,
  listing_id uuid not null references listings(id) on delete cascade,
  price numeric not null,
  currency text,
  observed_at timestamptz not null default now()
);

create table blocked_sellers (
  id uuid primary key default gen_random_uuid(),
  marketplace text not null references marketplaces(code),
  seller_id text not null,
  seller_name text,
  scope text not null check (scope in ('personal','shared')),
  owner_id uuid references profiles(id) on delete cascade,
  created_by uuid not null references profiles(id) on delete cascade,
  reason text,
  created_at timestamptz not null default now(),
  check ((scope = 'personal') = (owner_id is not null))
);
create unique index blocked_sellers_unique
  on blocked_sellers (marketplace, seller_id, scope, coalesce(owner_id, '00000000-0000-0000-0000-000000000000'));

create table worker_heartbeat (
  id int primary key default 1 check (id = 1),
  last_started_at timestamptz,
  last_finished_at timestamptz,
  last_success_at timestamptz,
  last_status text,
  summary jsonb
);

-- Indexes
create index on matches (user_id, matched_at desc);
create index on matches (listing_id);
create index on matches (notified_at) where notified_at is null;
create index on saved_searches (user_id);
create index on price_history (listing_id, observed_at);
create index on seen_listings (last_seen_at);
create index on listing_feedback (listing_id);

-- Seed
insert into marketplaces (code, name) values
  ('ebay','eBay'), ('grailed','Grailed'), ('mercari_jp','Mercari Japan');
insert into worker_heartbeat (id) values (1);

-- ---------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, split_part(coalesce(new.email, ''), '@', 1))
  on conflict (id) do nothing;
  return new;
end $$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create trigger saved_searches_touch
  before update on saved_searches
  for each row execute function public.touch_updated_at();

-- Re-run the silent first poll when the query or marketplaces of a search change,
-- so editing a search never floods the user's phone.
create or replace function public.reset_search_state_on_query_change()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if new.query is distinct from old.query then
    update search_state set initialized = false, last_polled_at = null where search_id = new.id;
  end if;
  return new;
end $$;

create trigger saved_searches_reset_state
  after update on saved_searches
  for each row execute function public.reset_search_state_on_query_change();

create or replace function public.reset_search_state_on_override_change()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  sid uuid := coalesce(new.search_id, old.search_id);
  mp text := coalesce(new.marketplace, old.marketplace);
begin
  if tg_op = 'UPDATE' and new.query_override is not distinct from old.query_override then
    return new;
  end if;
  update search_state set initialized = false, last_polled_at = null
   where search_id = sid and marketplace = mp;
  return coalesce(new, old);
end $$;

create trigger overrides_reset_state
  after insert or update or delete on search_marketplace_overrides
  for each row execute function public.reset_search_state_on_override_change();

-- ---------------------------------------------------------------------------
-- Storage pruning (README 4.7). Called by the worker via RPC with the service role.
-- ---------------------------------------------------------------------------

create or replace function public.prune_storage(
  match_days int default 60,
  seen_days int default 120
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  n_matches int; n_listings int; n_seen int;
begin
  delete from matches m
   where m.matched_at < now() - make_interval(days => match_days)
     and not exists (
       select 1 from listing_feedback f
        where f.listing_id = m.listing_id and f.user_id = m.user_id and f.feedback = 'like');
  get diagnostics n_matches = row_count;

  -- price_history goes with its listing (on delete cascade)
  delete from listings l
   where not exists (select 1 from matches m where m.listing_id = l.id)
     and not exists (select 1 from listing_feedback f where f.listing_id = l.id and f.feedback = 'like');
  get diagnostics n_listings = row_count;

  delete from seen_listings where last_seen_at < now() - make_interval(days => seen_days);
  get diagnostics n_seen = row_count;

  return jsonb_build_object('matches', n_matches, 'listings', n_listings, 'seen_listings', n_seen);
end $$;

revoke all on function public.prune_storage(int, int) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------

-- Explicit grants (newer Supabase projects don't always auto-grant). RLS below
-- still decides which rows each user can touch; anon gets nothing.
grant usage on schema public to authenticated, service_role;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant all on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to authenticated, service_role;
revoke all on all tables in schema public from anon;

alter table profiles enable row level security;
alter table marketplaces enable row level security;
alter table saved_searches enable row level security;
alter table search_marketplace_overrides enable row level security;
alter table search_state enable row level security;
alter table seen_listings enable row level security;
alter table listings enable row level security;
alter table matches enable row level security;
alter table listing_feedback enable row level security;
alter table price_history enable row level security;
alter table blocked_sellers enable row level security;
alter table worker_heartbeat enable row level security;

-- profiles: everyone signed in can read; users update only their own safe columns
create policy profiles_read on profiles for select to authenticated using (true);
create policy profiles_update_own on profiles for update to authenticated
  using (id = auth.uid()) with check (id = auth.uid());
revoke update on profiles from authenticated;
grant update (display_name, notify_mode, telegram_link_code) on profiles to authenticated;

-- saved_searches: full CRUD on own rows
create policy searches_own on saved_searches for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy overrides_own on search_marketplace_overrides for all to authenticated
  using (exists (select 1 from saved_searches s where s.id = search_id and s.user_id = auth.uid()))
  with check (exists (select 1 from saved_searches s where s.id = search_id and s.user_id = auth.uid()));

create policy search_state_read_own on search_state for select to authenticated
  using (exists (select 1 from saved_searches s where s.id = search_id and s.user_id = auth.uid()));

-- read-only shared tables
create policy listings_read on listings for select to authenticated using (true);
create policy price_history_read on price_history for select to authenticated using (true);
create policy marketplaces_read on marketplaces for select to authenticated using (true);
create policy heartbeat_read on worker_heartbeat for select to authenticated using (true);

-- matches: read own, update only `state`
create policy matches_read_own on matches for select to authenticated using (user_id = auth.uid());
create policy matches_update_own on matches for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
revoke update on matches from authenticated;
grant update (state) on matches to authenticated;

-- listing_feedback: full CRUD on own rows
create policy feedback_own on listing_feedback for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- blocked_sellers
create policy blocked_read on blocked_sellers for select to authenticated
  using (scope = 'shared' or owner_id = auth.uid());
create policy blocked_insert on blocked_sellers for insert to authenticated
  with check (
    created_by = auth.uid() and (
      (scope = 'personal' and owner_id = auth.uid()) or
      (scope = 'shared' and owner_id is null)
    ));
create policy blocked_delete on blocked_sellers for delete to authenticated
  using ((scope = 'shared' and created_by = auth.uid()) or owner_id = auth.uid());

-- seen_listings: no policies => no access for anon/authenticated
