"""CLI entry point.

    python -m sourcer run   [--max-runtime 150] [--dry-run] [--only-marketplace grailed]
                            [--only-search <uuid>] [--query "yohji pour homme" --marketplaces ebay,grailed]
    python -m sourcer prune
    python -m sourcer link-telegram
    python -m sourcer probe <marketplace> "<query>" [--save]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

from .adapters import ALL_CODES, AdapterNotConfigured, build_adapters
from .config import WORKER_DIR, Settings
from .models import SavedSearch, SearchOverride
from .notify import Telegram, link_telegram_accounts
from .prune import prune
from .runner import RunOptions, run
from .store import DryRunStore, MemoryStore, SupabaseStore


def _need_supabase(settings: Settings) -> SupabaseStore:
    if not settings.has_supabase:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (see worker/.env.example)")
    return SupabaseStore(settings.supabase_url, settings.supabase_service_role_key)  # type: ignore[arg-type]


async def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    adapters = build_adapters(settings)
    options = RunOptions(
        max_runtime=args.max_runtime,
        dry_run=args.dry_run,
        only_marketplace=args.only_marketplace,
        only_search=args.only_search,
        force=args.dry_run or bool(args.only_search),
    )
    if args.query:
        if not args.dry_run:
            sys.exit("--query is only allowed together with --dry-run")
        mps = [m.strip() for m in (args.marketplaces or ",".join(ALL_CODES)).split(",") if m.strip()]
        overrides = {}
        if args.mercari_query:
            overrides["mercari_jp"] = SearchOverride(marketplace="mercari_jp", query_override=args.mercari_query)
        search = SavedSearch(
            id="adhoc", user_id="dry-run", title=args.query, query=args.query,
            marketplaces=mps, overrides=overrides, spam_filter_enabled=not args.no_spam_filter,
        )
        store = MemoryStore(searches=[search])
    elif args.dry_run:
        store = DryRunStore(_need_supabase(settings))
    else:
        store = _need_supabase(settings)

    telegram = None if args.dry_run or not settings.has_telegram else Telegram(settings)
    try:
        summary = await run(settings, store, adapters, options, telegram)
    finally:
        await store.aclose()
        if telegram:
            await telegram.aclose()
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary.get("status") in ("ok", "partial") else 1


async def cmd_prune(_: argparse.Namespace, settings: Settings) -> int:
    store = _need_supabase(settings)
    try:
        print(json.dumps(await prune(store), indent=2))
    finally:
        await store.aclose()
    return 0


async def cmd_link(_: argparse.Namespace, settings: Settings) -> int:
    if not settings.has_telegram:
        sys.exit("TELEGRAM_BOT_TOKEN must be set")
    store = _need_supabase(settings)
    tg = Telegram(settings)
    try:
        print(f"linked {await link_telegram_accounts(tg, store)} account(s)")
    finally:
        await store.aclose()
        await tg.aclose()
    return 0


async def cmd_probe(args: argparse.Namespace, settings: Settings) -> int:
    """Make ONE request to a marketplace and report what came back.

    Use it to verify an adapter (README 6.3) and, with --save, to capture a real
    fixture for the parser tests.
    """
    adapter = build_adapters(settings)[args.marketplace]
    raw: dict[str, object] = {}

    async def capture(resp: httpx.Response) -> None:
        await resp.aread()
        raw["status"] = resp.status_code
        raw["content_type"] = resp.headers.get("content-type")
        raw["url"] = str(resp.request.url)
        try:
            raw["body"] = resp.json()
        except ValueError:
            raw["body_text"] = resp.text[:2000]
            raw["text"] = resp.text

    async with httpx.AsyncClient(timeout=20, event_hooks={"response": [capture]}) as client:
        try:
            page = await adapter.search(client, args.query)
        except AdapterNotConfigured as e:
            print(f"not configured: {e}")
            return 2
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")
            print(json.dumps({k: v for k, v in raw.items() if k not in ("body", "text")}, indent=2, ensure_ascii=False))
            return 1
    print(f"HTTP {raw.get('status')}  {len(page.listings)} listings  next_cursor={page.next_cursor!r}")
    for x in page.listings[:10]:
        print(f"  {x.listed_at}  {x.price} {x.currency}  seller={x.seller_id}  {x.title[:60]!r}")
    payload = raw.get("body")
    if payload is None and "text" in raw and hasattr(adapter, "parse_html"):
        # HTML page (Yahoo): keep only the results block of __NEXT_DATA__ as the fixture.
        from .adapters.yahoo_auctions import _listing_block, extract_next_data

        block = _listing_block(extract_next_data(str(raw["text"])))
        payload = {"props": {"pageProps": {"initialState": {"search": {"items": {"listing": block}}}}}}
    if args.save and payload is not None:
        out = WORKER_DIR / "tests" / "fixtures" / f"{args.marketplace}_real.json"
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        await asyncio.to_thread(Path(out).write_text, text, encoding="utf-8")
        print(f"saved real response to {out}; run pytest (tests/test_real_fixtures.py checks it)")
    return 0


async def cmd_status(_: argparse.Namespace, settings: Settings) -> int:
    """Summarise what's in Supabase: users, Telegram links, searches, polls, matches, heartbeat."""
    if not settings.supabase_url or "YOUR-PROJECT" in settings.supabase_url:
        sys.exit("SUPABASE_URL in worker/.env is still the placeholder; paste your Project URL "
                 "(Supabase > Project Settings > API)")
    store = _need_supabase(settings)
    try:
        try:
            profiles = await store._get("profiles", {"select": "id,display_name,telegram_chat_id,notify_mode"})
        except httpx.HTTPStatusError as e:
            sys.exit(f"Supabase rejected the request (HTTP {e.response.status_code}). "
                     "Check SUPABASE_URL and that SUPABASE_SERVICE_ROLE_KEY is the service_role key.")
        mps = await store._get("marketplaces", {"select": "code,enabled,paused_until"})
        searches = await store._get("saved_searches", {"select": "id,user_id,title,marketplaces,is_paused"})
        states = await store._get("search_state", {"select": "search_id,marketplace,initialized,last_polled_at,last_run_stats,last_error"})
        matches = await store._get("matches", {"select": "user_id,is_initial,notified_at"})
        hb = await store._get("worker_heartbeat", {"select": "*"})
    finally:
        await store.aclose()

    print(f"Marketplaces: {', '.join(m['code'] + ('' if m['enabled'] else ' (disabled)') for m in mps)}")
    if "yahoo_auctions" not in {m["code"] for m in mps}:
        print("  !! yahoo_auctions missing: run supabase/migrations/0002_yahoo_auctions.sql")
    print(f"\nUsers: {len(profiles)}")
    for p in profiles:
        tg = "Telegram linked" if p["telegram_chat_id"] else "Telegram NOT linked"
        print(f"  {p['display_name'] or p['id'][:8]}: {tg}, notify={p['notify_mode']}")
    print(f"\nSaved searches: {len(searches)}")
    by_search: dict[str, list[dict]] = {}
    for st in states:
        by_search.setdefault(st["search_id"], []).append(st)
    for s in searches:
        print(f"  '{s['title']}'{' (paused)' if s['is_paused'] else ''}")
        for mp in s["marketplaces"]:
            st = next((x for x in by_search.get(s["id"], []) if x["marketplace"] == mp), None)
            if not st:
                print(f"    {mp:<15} never polled")
                continue
            stats = st.get("last_run_stats") or {}
            phase = "live" if st["initialized"] else "first run pending"
            print(f"    {mp:<15} {phase}, last polled {st['last_polled_at']}, last run: "
                  f"fetched {stats.get('fetched', 0)}, new {stats.get('new', 0)}, matched {stats.get('matched', 0)}"
                  + (f"  ERROR: {st['last_error']}" if st.get("last_error") else ""))
    initial = sum(1 for m in matches if m["is_initial"])
    live = len(matches) - initial
    pending = sum(1 for m in matches if not m["notified_at"])
    print(f"\nMatches: {len(matches)} ({initial} initial/silent, {live} live alerts, {pending} waiting to be sent)")
    h = hb[0] if hb else {}
    print(f"Last worker run: {h.get('last_finished_at') or 'never'} status={h.get('last_status')}")
    return 0


async def cmd_telegram_check(args: argparse.Namespace, settings: Settings) -> int:
    """Setup helper: verify the bot token, list chats that messaged the bot, send a test.

    Reads updates WITHOUT confirming them, so it never interferes with link-telegram.
    """
    if not settings.has_telegram:
        sys.exit("TELEGRAM_BOT_TOKEN is not set in worker/.env")
    tg = Telegram(settings)
    try:
        me = (await tg._call("getMe"))["result"]
        print(f"Bot OK: @{me['username']} ({me.get('first_name')})")
        print(f"  -> web/.env.local: NEXT_PUBLIC_TELEGRAM_BOT_USERNAME={me['username']}")
        chats: dict[int, str] = {}
        for u in await tg.get_updates():
            chat = (u.get("message") or {}).get("chat") or {}
            if "id" in chat:
                name = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
                chats[chat["id"]] = f"{chat.get('type')} {name}".strip()
        print()
        if not chats:
            print("No messages found. Send any message to the bot in Telegram, then re-run.")
        else:
            print("Chats that messaged the bot (last 24 h):")
            for cid, desc in chats.items():
                print(f"  {cid}   {desc}")
            print("  -> pick yours and set TELEGRAM_ADMIN_CHAT_ID=<id> in worker/.env")
        target = args.send_test or settings.telegram_admin_chat_id
        if target:
            await tg.send_message(target, "✅ Sourcer test message: admin alerts will arrive here.")
            print(f"Test message sent to {target}.")
    finally:
        await tg.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sourcer")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="poll marketplaces")
    r.add_argument("--max-runtime", type=float, default=150)
    r.add_argument("--dry-run", action="store_true", help="fetch and match, print, write/send nothing")
    r.add_argument("--only-marketplace", choices=ALL_CODES)
    r.add_argument("--only-search")
    r.add_argument("--query", help="(dry run) ad-hoc search instead of reading Supabase")
    r.add_argument("--marketplaces", help="(with --query) comma-separated codes")
    r.add_argument("--mercari-query", help="(with --query) Mercari JP query override")
    r.add_argument("--no-spam-filter", action="store_true", help="(with --query) disable spam filter")

    sub.add_parser("prune", help="storage cleanup")
    sub.add_parser("link-telegram", help="process /start <code> messages")
    sub.add_parser("status", help="summarise what is in Supabase (setup check)")
    tc = sub.add_parser("telegram-check", help="setup helper: verify bot token, find chat IDs")
    tc.add_argument("--send-test", metavar="CHAT_ID", help="send a test message to this chat")

    pr = sub.add_parser("probe", help="one request to a marketplace, to verify an adapter")
    pr.add_argument("marketplace", choices=ALL_CODES)
    pr.add_argument("query")
    pr.add_argument("--save", action="store_true", help="save the raw response as a test fixture")

    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    settings = Settings.from_env()
    handler = {"run": cmd_run, "prune": cmd_prune, "link-telegram": cmd_link, "probe": cmd_probe,
               "telegram-check": cmd_telegram_check, "status": cmd_status}[args.cmd]
    return asyncio.run(handler(args, settings))


if __name__ == "__main__":
    sys.exit(main())
