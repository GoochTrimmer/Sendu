# Sourcer (working name)

A personal sourcing tool for second-hand designer and archive clothing. Users save keyword searches; a scheduled worker runs them across several marketplaces, detects **new** listings, filters out spam and blocked sellers, and sends alerts to Telegram. A small web app is used to manage searches, review matches, like/dismiss listings and maintain seller blocklists.

This README is the build spec. It is written to be handed to Claude Code (or any developer) to implement. Build **Phase 1 (MVP)** first; later phases are listed so the MVP's data model doesn't paint us into a corner.

---

## 1. Context and goals

- **Users:** the owner plus a small group of friends (roughly 2–10 people). Invite-only. Not a public product, not for customers.
- **Main use case:** e.g. tracking Yohji Yamamoto / Y's / Pour Homme pieces across Japanese and global marketplaces, and being notified quickly when something new is listed.
- **Priorities:** catching new listings reliably > speed > polish. Keep it cheap (free tiers), lean on storage, and gentle on the marketplaces.

### Non-goals (for now)
- Buying, bidding, or messaging sellers through the app.
- Logging into marketplace accounts.
- Storing images.
- Serving anyone outside the friend group.

---

## 2. Architecture

```
┌────────────────────┐        ┌───────────────────────────┐
│ Web app (Next.js)  │◄──────►│ Supabase                  │
│ hosted on Vercel   │        │ Postgres + Auth + RLS     │
└────────────────────┘        └─────────────▲─────────────┘
                                            │ service-role key
                              ┌─────────────┴─────────────┐
                              │ Worker (Python)           │
                              │ run by GitHub Actions cron│──► Marketplaces
                              │ (later: Raspberry Pi/PC)  │──► Telegram Bot API
                              └───────────────────────────┘
```

- The **web app never scrapes**. It only reads and writes Supabase.
- The **worker** is a plain Python CLI. Nothing in it may depend on GitHub Actions specifically, so it can later run unchanged on a Raspberry Pi or PC with a systemd timer or cron.
- The worker talks only **outbound**: to marketplaces, Supabase and Telegram.

### Stack
| Part | Tech |
|---|---|
| Web | Next.js (App Router, TypeScript), Tailwind CSS, `@supabase/ssr` |
| Database / auth | Supabase (Postgres, email + password auth, Row Level Security) |
| Worker | Python 3.12, `httpx` (async), `pydantic`, `supabase-py` (or `psycopg` directly), `selectolax` for HTML parsing if needed |
| Scheduler | GitHub Actions `schedule` |
| Notifications | Telegram Bot API |
| Hosting | Vercel (web), GitHub Actions (worker) |

No Playwright or headless browser in Phase 1 (it's slow and expensive in GitHub Actions minutes). Adapters should use official APIs or the JSON endpoints that sites' own frontends call.

---

## 3. Repository layout

```
/
├── README.md
├── supabase/
│   └── migrations/
│       └── 0001_init.sql
├── worker/
│   ├── pyproject.toml
│   ├── sourcer/
│   │   ├── __main__.py        # CLI entry: python -m sourcer run|prune|link-telegram
│   │   ├── config.py          # env vars, constants
│   │   ├── models.py          # pydantic: Listing, SearchPage, SavedSearch...
│   │   ├── normalize.py       # text normalisation (NFKC, case, Y's/ys...)
│   │   ├── matching.py        # include/exclude, price, size, spam, blocklist
│   │   ├── scheduler.py       # picks which (search, marketplace) pairs to run
│   │   ├── ratelimit.py       # per-marketplace limiter with jitter + backoff
│   │   ├── store.py           # all Supabase reads/writes
│   │   ├── notify.py          # Telegram send + link-code handling
│   │   ├── prune.py           # storage cleanup
│   │   └── adapters/
│   │       ├── base.py        # MarketplaceAdapter protocol
│   │       ├── ebay.py
│   │       ├── grailed.py
│   │       └── mercari_jp.py
│   ├── data/
│   │   └── brands.txt         # brand list used by the spam filter
│   └── tests/
│       ├── fixtures/          # saved real API responses per adapter
│       └── test_*.py
├── web/
│   └── (Next.js app)
└── .github/workflows/
    ├── poll.yml               # scheduled worker run
    ├── prune.yml              # daily cleanup
    └── ci.yml                 # tests + lint on push
```

---

## 4. Core behaviour (read this before writing code)

### 4.1 Every saved search runs as its own query
Each saved search runs **directly on each selected marketplace, exactly as the user typed it**. Marketplace search engines are inconsistent (different word matching, relevance mixed into "newest", listings without a brand tag), so don't merge searches into broad brand feeds in Phase 1. A merge/coverage optimiser is Phase 2 and will only ever *suggest* merges.

A saved search can have a **per-marketplace query override**, e.g. query `yohji pour homme` everywhere but `ヨウジヤマモト プールオム` on Mercari JP.

### 4.2 Request budget per marketplace
Because there may be hundreds of saved searches, the worker never does "run everything". Each run:

1. Each marketplace has a **per-run request budget** (configurable; default derived from the runtime cap ÷ request interval).
2. The scheduler builds a queue of `(saved_search, marketplace)` pairs and orders them by **due-ness**:
   `overdue_ratio = (now - last_polled_at) / target_interval(priority)`
   with target intervals: `urgent` = 30 min, `normal` = 2 h, `daily` = 24 h (configurable). Pairs with `overdue_ratio < 1` are skipped.
3. Marketplaces are processed **concurrently** (one async task each); requests *within* a marketplace are sequential and rate-limited.
4. The run stops cleanly at `--max-runtime` seconds, saving progress so the next run continues from where it stopped.

Adding more searches never raises the request rate. It only makes low-priority searches run less often.

### 4.3 Rate limiting and backoff (do not skip)
- At least **6 s between requests to the same marketplace**, plus random jitter of 0–3 s.
- Send normal browser-like headers (User-Agent, Accept-Language). Don't rotate identities or proxies to evade blocks.
- On HTTP **429**, **403**, or a detected captcha/challenge page: stop using that marketplace, set `marketplaces.paused_until = now + backoff` (1 h, doubling on each consecutive failure up to 24 h), and send **one** Telegram alert to the admin chat. Don't retry in the same run.
- On 5xx or network errors: retry once after 10 s, then skip and record `last_error`.
- **Never log in, never solve or bypass captchas.** Anonymous public search only.

### 4.4 Paging and detecting new listings
Request results **sorted newest first**. For each `(search, marketplace)` pair, page through results until **any** of these is true:
- 5 consecutive listings already exist in `seen_listings` (ordering is not strictly reliable, so don't stop at the first one), or
- a listing's `listed_at` is older than `last_polled_at - 2 h`, or
- 3 pages have been read, or
- the marketplace's budget is used up.

A listing is **new** if `(marketplace, external_id)` is not in `seen_listings`. If it *is* seen but its price changed, record a price-history row and flag it as a price drop.

**Bumps:** if a seen listing reappears at the top with a newer `listed_at` (e.g. a Grailed bump), don't alert. Update `last_seen_at` and set `is_bumped = true`.

**First run of a new search (important):** the first time a `(search, marketplace)` pair runs, mark everything returned as seen and create matches with `notified_at` set, **without sending Telegram alerts**. Otherwise every new search floods the user's phone. The web feed can still show them, labelled "initial results".

### 4.5 Matching pipeline
For each new listing returned by a saved search's query, apply in order:

1. **Normalise** title + description: Unicode NFKC (full-width → half-width for Japanese), lower-case, collapse whitespace, unify variants (`y's` / `ys` / `y’s`).
2. **Blocked seller:** drop if `seller_id` is in the user's personal blocklist or the shared blocklist for that marketplace. Match on **seller ID**, not username (usernames change).
3. **Include terms:** every term in `include_terms` must appear (optional field; the marketplace query already filtered roughly).
4. **Exclude terms** (only if `exclude_enabled`, which can be overridden per marketplace): drop if any exclude term appears.
5. **Spam filter** (only if `spam_filter_enabled`): count distinct brand names from `worker/data/brands.txt` found in title + description. If the count is **≥ 3** (configurable), drop. This catches keyword-stuffed bot listings.
6. **Price** (`min_price`/`max_price`, converted to the search's currency using a fixed rate table in config for Phase 1) and **size** filters if set.

Keep counters of how many listings each filter dropped, per search per run, and store them in `search_state.last_run_stats` so the UI can show "12 hidden as spam, 3 from blocked sellers".

A listing that passes is **stored in full** in `listings` and gets a row in `matches` for that search's owner. Listings that matched nothing only go into `seen_listings` (ID + timestamps + price). This keeps storage small.

### 4.6 Notifications (Telegram)
- One bot (token in env). Each user links their Telegram account: the web settings page shows a one-time code; the user sends `/start <code>` to the bot; the worker's `link-telegram` step (run at the start of every poll) reads `getUpdates` and saves `telegram_chat_id` on the profile.
- Each new match → `sendPhoto` with the first image URL and a caption: title, price + currency, size, marketplace, seller, link. Fall back to `sendMessage` if the photo fails.
- If a user has more than 5 new matches in one run, send a **single digest message** instead (top 10 with links, plus "N more in the app").
- Set `matches.notified_at` after sending. Never send the same match twice.

### 4.7 Storage hygiene (Supabase free tier = 500 MB)
- **No images stored.** Keep at most 4 image URLs per listing.
- `description_snippet` truncated to 500 characters.
- Daily `prune` job:
  - delete `matches` older than 60 days with no like;
  - delete `listings` that no longer have any match or like;
  - delete `seen_listings` older than 120 days;
  - keep `price_history` only for listings that still exist.
- Liked listings, blocklists and saved searches are never pruned.

### 4.8 Heartbeat / watchdog
At the start and end of every run, the worker writes to `worker_heartbeat` (start time, finish time, status, per-marketplace summary). The web app shows a warning banner if the last successful run finished more than 3 h ago. If a run ends in error, also message the admin chat.

---

## 5. Database schema (`supabase/migrations/0001_init.sql`)

Implement this as the first migration. Adjust types where clearly needed, but keep the table names and intent.

```sql
-- Profiles (1:1 with auth.users)
create table profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  telegram_chat_id bigint,
  telegram_link_code text unique,
  notify_mode text not null default 'instant' check (notify_mode in ('instant','digest','off')),
  is_admin boolean not null default false,
  created_at timestamptz not null default now()
);

-- Marketplaces (seeded; managed by the worker)
create table marketplaces (
  code text primary key,                 -- 'ebay', 'grailed', 'mercari_jp'
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
  marketplaces text[] not null,          -- marketplace codes
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

-- Per-marketplace overrides for a saved search
create table search_marketplace_overrides (
  search_id uuid references saved_searches(id) on delete cascade,
  marketplace text references marketplaces(code),
  query_override text,                   -- e.g. Japanese query for Mercari JP
  exclude_enabled boolean,               -- null = inherit
  extra_exclude_terms text[] not null default '{}',
  primary key (search_id, marketplace)
);

-- Worker state per (search, marketplace)
create table search_state (
  search_id uuid references saved_searches(id) on delete cascade,
  marketplace text references marketplaces(code),
  initialized boolean not null default false,  -- false until the silent first run
  last_polled_at timestamptz,
  last_new_count int,
  last_run_stats jsonb,                  -- {fetched, new, matched, spam, blocked, excluded}
  consecutive_errors int not null default 0,
  primary key (search_id, marketplace)
);

-- Lightweight record of every listing ever seen (dedup)
create table seen_listings (
  marketplace text not null,
  external_id text not null,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  last_price numeric,
  primary key (marketplace, external_id)
);

-- Full rows only for listings that matched at least one search
create table listings (
  id uuid primary key default gen_random_uuid(),
  marketplace text not null references marketplaces(code),
  external_id text not null,
  title text not null,
  description_snippet text,
  price numeric,
  currency text,
  size text,
  brand text,
  seller_id text,
  seller_name text,
  url text not null,
  image_urls text[] not null default '{}',
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
  is_initial boolean not null default false,  -- came from the silent first run
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

-- Price history: insert only when the price changes
create table price_history (
  id bigserial primary key,
  listing_id uuid not null references listings(id) on delete cascade,
  price numeric not null,
  currency text,
  observed_at timestamptz not null default now()
);

-- Seller blocklists: personal (owner set) or shared (owner null)
create table blocked_sellers (
  id uuid primary key default gen_random_uuid(),
  marketplace text not null references marketplaces(code),
  seller_id text not null,
  seller_name text,
  scope text not null check (scope in ('personal','shared')),
  owner_id uuid references profiles(id) on delete cascade,  -- required when personal
  created_by uuid not null references profiles(id),
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
  last_status text,
  summary jsonb
);

-- Useful indexes
create index on matches (user_id, matched_at desc);
create index on saved_searches (user_id);
create index on price_history (listing_id, observed_at);
create index on seen_listings (last_seen_at);

-- Seed
insert into marketplaces (code, name) values
  ('ebay','eBay'), ('grailed','Grailed'), ('mercari_jp','Mercari Japan');
insert into worker_heartbeat (id) values (1);
```

### Row Level Security
Enable RLS on every table. The worker uses the **service-role key** and bypasses RLS. The web app uses the anon key + the user's session.

| Table | Authenticated users can |
|---|---|
| `profiles` | read all display names; update only their own row |
| `saved_searches`, `search_marketplace_overrides` | full CRUD on their own rows only |
| `search_state` | read rows for their own searches |
| `listings`, `price_history`, `marketplaces`, `worker_heartbeat` | read only |
| `matches` | read and update `state` on their own rows |
| `listing_feedback` | full CRUD on their own rows |
| `blocked_sellers` | read shared rows + their own personal rows; insert personal (owner = self) or shared; delete shared rows they created and their own personal rows |
| `seen_listings` | no access |

**Invite-only:** disable public sign-ups in Supabase Auth and create users from the dashboard with an email and password (no emails are sent). Create the `profiles` row with a trigger on `auth.users` insert.

---

## 6. Worker details

### 6.1 Normalised listing model
```python
class Listing(BaseModel):
    marketplace: str
    external_id: str
    title: str
    description: str | None
    price: Decimal | None
    currency: str | None
    size: str | None
    brand: str | None
    seller_id: str | None
    seller_name: str | None
    url: str
    image_urls: list[str]       # first 4 only
    listed_at: datetime | None  # when it was listed or last bumped, if available
    raw_status: str | None      # sold/active if the API says
```

### 6.2 Adapter interface
```python
class SearchPage(BaseModel):
    listings: list[Listing]
    next_cursor: str | None

class MarketplaceAdapter(Protocol):
    code: str
    async def search(self, client: httpx.AsyncClient, query: str,
                     cursor: str | None = None) -> SearchPage: ...
    def is_blocked_response(self, resp: httpx.Response) -> bool: ...
```
Adapters only fetch and parse. Rate limiting, paging stop rules, dedup and matching all live outside the adapters.

### 6.3 Phase 1 adapters
Before writing each adapter, **inspect the site's current behaviour** (official docs or the browser network tab) and record what you found in a comment at the top of the file: the endpoint, parameters, sort-by-newest option, and where the seller ID lives. Endpoints change, so don't rely on assumptions.

| Marketplace | Approach | Notes |
|---|---|---|
| **eBay** | Official **Browse API** (`item_summary/search`), OAuth client-credentials token | Needs an eBay developer app (`EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`). Sort by newly listed. Lowest ban risk. |
| **Grailed** | The JSON search endpoint the Grailed web frontend uses (historically an Algolia index; verify) | Sort by newest. Seller ID needed for the blocklist (bot accounts such as mass resellers of Xianyu listings are the main reason the blocklist exists). |
| **Mercari JP** | The JSON search endpoint the Mercari JP web frontend uses (verify current auth/headers) | Sort by newest. **Test from a GitHub Actions runner early.** Runners are outside Japan, and the site may restrict or redirect overseas traffic. If it's blocked, report back; don't try to bypass it. |

If a site needs anything beyond normal headers (signed tokens, a JS challenge), implement only what the site's own public frontend does without login. If that isn't possible, stop and write up the findings instead of working around protection.

Save one real response per adapter in `tests/fixtures/` and write parser tests against it.

### 6.4 CLI
```
python -m sourcer run   [--max-runtime 150] [--dry-run] [--only-marketplace grailed] [--only-search <uuid>]
python -m sourcer prune
python -m sourcer link-telegram
```
`--dry-run`: fetch and match, print the results, write nothing, send nothing. Use it for local development.

### 6.5 Environment variables
| Var | Used by | Notes |
|---|---|---|
| `SUPABASE_URL` | worker, web | |
| `SUPABASE_SERVICE_ROLE_KEY` | worker only | Never expose to the web app |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | web | |
| `TELEGRAM_BOT_TOKEN` | worker | |
| `TELEGRAM_ADMIN_CHAT_ID` | worker | Alerts about blocks and failures |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | worker | |
| `REQUEST_INTERVAL_SECONDS` | worker | default 6 |
| `SPAM_BRAND_THRESHOLD` | worker | default 3 |

Provide a `worker/.env.example` and `web/.env.example`. In GitHub, store these as **Actions secrets**; in Vercel, as project environment variables.

---

## 7. GitHub Actions

### 7.1 Minutes budget (decision needed)
- **Public repo:** standard GitHub-hosted runners are free with no minute cap, so the worker can run every 30 min. Secrets stay private in Actions secrets; only the code is visible.
- **Private repo:** about 2,000 free minutes per month. Each run costs its full wall-clock time (rounded up to the minute) plus setup. Every 30 min is 1,440 runs a month, which only fits if runs are about 1 minute. **Default for a private repo: hourly, with `--max-runtime 90`.**

Make the cron schedule and `--max-runtime` easy to change. Cache pip dependencies to keep setup short.

Note: scheduled runs can start 5–15 minutes late under load, and in public repos scheduled workflows are disabled after 60 days with no repo activity (re-enable from the Actions tab). The heartbeat banner covers both.

### 7.2 `poll.yml` (sketch)
```yaml
name: poll
on:
  schedule:
    - cron: "*/30 * * * *"     # use "0 * * * *" for a private repo
  workflow_dispatch: {}
concurrency:
  group: poll
  cancel-in-progress: false     # never run two pollers at once
jobs:
  poll:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    defaults: { run: { working-directory: worker } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -e .
      - run: python -m sourcer run --max-runtime 150
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_ADMIN_CHAT_ID: ${{ secrets.TELEGRAM_ADMIN_CHAT_ID }}
          EBAY_CLIENT_ID: ${{ secrets.EBAY_CLIENT_ID }}
          EBAY_CLIENT_SECRET: ${{ secrets.EBAY_CLIENT_SECRET }}
```
`prune.yml`: same setup, `cron: "17 19 * * *"` (daily, 03:17 Singapore time), runs `python -m sourcer prune`.
`ci.yml`: on push/PR, run `pytest` and `ruff` for the worker and `lint` + `typecheck` for the web app.

---

## 8. Web app (Phase 1 pages)

Simple, mobile-friendly (most use will be on phones), dark mode supported.

| Route | Purpose |
|---|---|
| `/login` | Email + password sign-in (change password in Settings) |
| `/` (Feed) | New matches across all of the user's searches, newest first. Filters: search, group, marketplace, "hide initial results". Each card: image, title, price, size, marketplace, seller, time listed, which search matched, badges (price drop, bumped). Actions: **Like**, **Dismiss**, **Block seller** (choose personal or shared), **Open listing**. Clicking a card marks it seen. |
| `/searches` | Saved searches grouped by `group_name`. Per search: status (active/paused), priority, marketplaces, last polled, 7-day matches and likes, last-run filter counts (spam/blocked/excluded). Create / edit / duplicate / pause / delete. |
| `/searches/new`, `/searches/[id]` | Form: title, query, marketplaces (checkboxes), per-marketplace query override, include terms, exclude terms + **exclude toggle** (with per-marketplace override), **spam filter toggle**, price range + currency, sizes, priority, group. Comma or newline-separated term entry. |
| `/liked` | Liked listings, kept permanently. |
| `/blocklist` | Personal and shared lists in tabs. Add a seller by pasting a profile URL or ID (parse marketplace + ID), see who added a shared entry and why, remove. |
| `/settings` | Display name, Telegram link code + status, notification mode (instant / digest / off). |
| Global | Banner if the worker heartbeat is stale or a marketplace is paused ("Mercari JP paused until 14:00: rate limited"). |

Use Supabase server-side auth in the App Router. All data access goes through RLS; no service key in the web app.

---

## 9. Phase plan

### Phase 1: MVP (build now)
- [ ] Migration `0001_init.sql` with RLS policies and profile trigger
- [ ] Worker: models, normalisation, matching, rate limiter + backoff, scheduler with budgets and runtime cap, store, notify, prune
- [ ] Adapters: eBay, Grailed, Mercari JP (with fixtures and tests)
- [ ] Silent first run for new searches
- [ ] Price history rows written on price change (no UI yet)
- [ ] GitHub Actions: poll, prune, ci
- [ ] Web: login, feed, searches CRUD, liked, blocklist, settings, heartbeat banner
- [ ] `.env.example` files and a setup section (below) that works start to finish

**Done when:** a user can sign in, create "yohji pour homme" on all three marketplaces with a Japanese override for Mercari JP, and within one or two scheduled runs receive Telegram alerts for genuinely new listings only. Spam-stuffed listings are hidden when the toggle is on and shown when it's off. Blocking a seller from the feed stops their listings from appearing for that user (personal) or everyone (shared). Running `--dry-run` locally prints matches without writing anything.

### Phase 2
- Keyword suggestions from likes (term statistics: liked vs dismissed listings), accepted into a chosen group
- Coverage audit: periodically compare overlapping searches and *suggest* merges to save request budget
- More marketplaces: Yahoo! Auctions JP, Rakuma, Vinted, Carousell, 2nd Street, Vestiaire Collective
- Bulk add searches; per-group exclude lists
- Price history chart on listing detail

### Phase 3
- LLM attribute extraction on liked listings (line, season/era, garment type, material), stored as `jsonb`
- Embeddings with pgvector (small model, about 384 dimensions; only liked/disliked listings) for "more like this" and ranking
- **Discovery feeds:** narrow category feeds (e.g. unbranded men's outerwear) scored by image + text similarity to likes; results in a separate "Maybe" tab; vision-model tag reading on top candidates only
- Auction ending-soon alerts

### Phase 4
- Move the worker to a Raspberry Pi / home PC (same CLI, systemd timer), which lets it poll more often from a residential IP
- Optional Playwright adapters for sites that need a browser
- Optional ESP32 desk display showing the latest match

---

## 10. Setup

### 1. Supabase
1. Create a project at supabase.com (free tier). Pick the Singapore region.
2. **SQL Editor → New query**: paste all of `supabase/migrations/0001_init.sql` and run it. (Or `supabase db push` with the CLI.)
3. **Authentication → Sign In / Providers → Email**: keep Email enabled and **turn off "Allow new users to sign up"**.
4. **Authentication → Sign In / Providers**: keep *Email* enabled, turn **off** *Allow new users to sign up*.
5. **Authentication → Users → Add user → Create new user** for each friend: email + a starting password, tick *Auto Confirm User*. Their `profiles` row is created by a trigger; they can change the password in Settings. To reset a forgotten password, run in the SQL editor:
   `update auth.users set encrypted_password = extensions.crypt('NEW-PASSWORD', extensions.gen_salt('bf')) where email = 'friend@example.com';`
6. Make yourself admin: `update profiles set is_admin = true where id = (select id from auth.users where email = 'you@…');`
7. **Project Settings → API**: copy the Project URL, the `anon` key (web) and the `service_role` key (worker only).

### 2. Telegram
1. In Telegram, message **@BotFather** → `/newbot` → copy the token (`TELEGRAM_BOT_TOKEN`) and the bot username (`NEXT_PUBLIC_TELEGRAM_BOT_USERNAME`).
2. Admin chat ID: send any message to your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id` (`TELEGRAM_ADMIN_CHAT_ID`). Do this **before** the worker runs, because the worker consumes updates.
3. Users link their accounts on **/settings** → "Generate link code" → send `/start CODE` to the bot. It links on the next worker run.

### 3. eBay
1. Sign up at developer.ebay.com → **Application Keys** → create a **Production** keyset.
2. Copy *App ID (Client ID)* → `EBAY_CLIENT_ID` and *Cert ID (Client Secret)* → `EBAY_CLIENT_SECRET`. The Browse API with client-credentials needs no user consent.
3. Optional: `EBAY_MARKETPLACE_ID` (repo *variable*) to search e.g. `EBAY_GB` instead of `EBAY_US`.

### 4. GitHub Actions
1. Push this folder to a GitHub repo (`git init && git add . && git commit && git push`).
2. **Settings → Secrets and variables → Actions → Secrets**: add `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `GRAILED_ALGOLIA_API_KEY` (plus `GRAILED_ALGOLIA_APP_ID` / `GRAILED_ALGOLIA_INDEX` if they differ from the defaults).
3. Schedule: `poll.yml` runs **every 30 min with `--max-runtime 150`**, set for a **public** repo. For a private repo, change the cron to `"0 * * * *"` and the default runtime to `90` (§7.1).
4. **Actions → probe → Run workflow** with `mercari_jp` **before relying on it**, to confirm Mercari JP answers from a US runner (§6.3). If it reports blocked/HTML, leave `mercari_jp` disabled (`update marketplaces set enabled=false where code='mercari_jp'`) until the Phase 4 home-server worker.
5. Public repos: scheduled workflows are disabled after 60 days with no commits. The web banner tells you when that happens.

### 5. Vercel
1. Import the repo, set **Root Directory = `web`**.
2. Env vars: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_TELEGRAM_BOT_USERNAME`. **Never** the service-role key.
3. Deploy (`npx vercel deploy --prod` from the repo root).

Local web dev: `cd web && cp .env.example .env.local` (fill in) → `npm install` → `npm run dev`.

### 6. Worker locally / verifying adapters
```bash
cd worker
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                                  # fill in what you have
pytest                                                # 57 tests, no network

# Ad-hoc dry run: no Supabase needed, prints matches, writes/sends nothing
python -m sourcer run --dry-run --query "yohji pour homme" --mercari-query "ヨウジヤマモト プールオム"
# Dry run of your real saved searches (reads Supabase, writes nothing)
python -m sourcer run --dry-run --only-marketplace ebay

# One request to one marketplace; --save stores the real response as the test fixture
python -m sourcer probe grailed "yohji yamamoto" --save
```

**Grailed key:** open grailed.com/shop in a desktop browser → DevTools → Network → filter `algolia` → search something. In the `…/query` request, copy the `x-algolia-application-id`, `x-algolia-api-key` (the public search-only key the site sends from every visitor's browser) and the index name in the URL path (pick the "by date added" index) into the `GRAILED_ALGOLIA_*` vars. If Grailed no longer uses Algolia, the adapter needs rewriting; see the header of `worker/sourcer/adapters/grailed.py`.

---

## 11a. Implementation notes (Phase 1 build)

**Adapter verification status (§6.3).** Checked 2026-11-02 in a real browser (anonymous search pages) and with `probe` from a home connection in Singapore:
| Adapter | Approach | Status |
|---|---|---|
| Mercari JP | `POST api.mercari.jp/v2/entities:search` + DPoP proof the browser makes itself (no login) | **Verified, works.** Real capture in `tests/fixtures/mercari_jp_real.json`. Still to check from GitHub's US runners (`probe` workflow) |
| Yahoo! Auctions JP | Search page `auctions.yahoo.co.jp/search/search?s1=new&o1=d`, results from its `__NEXT_DATA__` JSON. Auctions + Buy It Now | **Verified, works.** Real capture in `tests/fixtures/yahoo_auctions_real.json`. Still to check from US runners; if blocked there, Buyee is the planned fallback |
| Grailed | Algolia index `Listing_by_date_added_production` ("Newly Listed") | **Blocked on the API key.** The site gets its key from `/api/algolia/keys`, which returns 404 to anonymous requests. Not yet known whether logged-out visitors get a key at all. We never use account credentials, so leave `GRAILED_ALGOLIA_API_KEY` unset until that's confirmed (check in a private/incognito window) |
| eBay | Official Browse API | Deferred; written to the docs, untested |

Real captures (`*_real.json`, from `probe <mp> "<query>" --save`) are checked by `tests/test_real_fixtures.py`. The `*_search.json` fixtures are hand-made in the verified shape for exact-value unit tests.

**Marketplace quirks found:**
- Mercari's "newest" sort orders by **last update**, not creation: price cuts push year-old items to the top. Listings therefore carry `listed_at` (real listing time, shown in the feed) and `sort_time` (what the site sorts by, used for the paging stop rule and bump detection). Re-surfaced items are flagged as bumped, never alerted.
- Mercari Shops items report `sellerId: "0"`; the adapter uses `shop:<shop id>` instead, so blocking one shop doesn't block all of them.
- Mercari's keyword search is loose (a Japanese query also returns Y's / Y-3 / unrelated items). Use **include terms** on a search to tighten it.
- Yahoo shows one promoted ("featured") item out of order per page; it's skipped. Seller display names are masked, so the feed shows the seller ID.

**Design decisions beyond the spec:**
- *Per-search newness.* `seen_listings` is global, so "seen" alone would let one search steal another's new listings. A listing is new to search S if it was first seen **after S's previous poll** (`scheduler.is_known`). The same rule drives the "5 consecutive known" paging stop.
- Editing a search's query (or a query override) resets it to a silent first run (DB trigger), so edits never flood your phone.
- Schema additions: `listings.previous_price/price_dropped_at` (price-drop badge), `seen_listings.last_listed_at` (bump detection), `search_state.last_error`, `worker_heartbeat.last_success_at`, `prune_storage()` SQL function, column-level grants so users can't set `is_admin`/`telegram_chat_id` or touch `notified_at`.
- One Telegram alert per listing per user, even if several of their searches matched it. Matches are marked notified one by one, so a mid-run Telegram failure never re-sends.
- Size filter keeps listings with **no** size (Mercari/eBay search results often omit it).
- Blocklist entries pasted as Grailed profile URLs only have a username; they're stored as `name:<username>` and matched on username. Blocking from a feed card stores the real ID.
- Duplicated searches start **paused** so you can edit them first.
- Web uses Next.js 16 with `cacheComponents` off (every page is per-user), and `proxy.ts` (Next 16's renamed middleware) for session refresh.

---

## 11. Ground rules for implementation

- Be gentle with marketplaces: rate limits and backoff are requirements, not nice-to-haves.
- Anonymous public search only. No logins, no captcha solving, no evasion of bot protection.
- Keep adapters thin and replaceable. When a site changes, only its adapter should need to change.
- Keep storage lean (section 4.7). Never store images.
- Prefer small, tested modules. Matching and paging stop rules need unit tests.
- When something about a marketplace can't be verified, write down what was tried and what's needed rather than guessing.
