"""Telegram alerts, digests, admin messages and account linking (README 4.6)."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import Settings
from .models import Listing, PendingMatch, Profile

log = logging.getLogger(__name__)

MARKETPLACE_NAMES = {
    "ebay": "eBay",
    "grailed": "Grailed",
    "mercari_jp": "Mercari JP",
    "yahoo_auctions": "Yahoo! Auctions",
}
START_RE = re.compile(r"^/start(?:@\w+)?\s+([A-Za-z0-9_-]{4,64})\s*$")


def format_price(listing: Listing) -> str:
    if listing.price is None:
        return "price n/a"
    amount = f"{listing.price:,.0f}" if listing.currency == "JPY" else f"{listing.price:,.2f}"
    return f"{amount} {listing.currency or ''}".strip()


def caption(m: PendingMatch) -> str:
    x = m.listing
    e = html.escape
    lines = [
        f"<b>{e(x.title[:200])}</b>",
        f"{e(format_price(x))}" + (f" · size {e(x.size)}" if x.size else ""),
        f"{e(MARKETPLACE_NAMES.get(x.marketplace, x.marketplace))}"
        + (f" · {e(x.seller_name or x.seller_id or '')}" if (x.seller_name or x.seller_id) else ""),
        f"🔎 {e(m.search_title)}",
        f'<a href="{e(x.url, quote=True)}">Open listing</a>',
    ]
    return "\n".join(lines)[:1024]


def digest(matches: list[PendingMatch], top_n: int) -> str:
    e = html.escape
    lines = [f"<b>{len(matches)} new matches</b>"]
    for m in matches[:top_n]:
        x = m.listing
        lines.append(
            f'• <a href="{e(x.url, quote=True)}">{e(x.title[:80])}</a> '
            f"— {e(format_price(x))} ({e(MARKETPLACE_NAMES.get(x.marketplace, x.marketplace))})"
        )
    if len(matches) > top_n:
        lines.append(f"…and {len(matches) - top_n} more in the app.")
    return "\n".join(lines)[:4096]


class Telegram:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.token = settings.telegram_bot_token
        self.admin_chat_id = settings.telegram_admin_chat_id
        self.client = client or httpx.AsyncClient(timeout=20)
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _call(self, method: str, **payload: Any) -> dict[str, Any]:
        r = await self.client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
        body = r.json() if r.content else {}
        if r.status_code == 429:  # Telegram flood control: wait as told, once
            await asyncio.sleep(float((body.get("parameters") or {}).get("retry_after", 3)))
            r = await self.client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
            body = r.json() if r.content else {}
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {body.get('description', r.status_code)}")
        return body

    async def send_message(self, chat_id: int | str, text: str) -> None:
        await self._call(
            "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True
        )

    async def send_match(self, chat_id: int | str, m: PendingMatch) -> None:
        text = caption(m)
        if m.listing.image_urls:
            try:
                await self._call(
                    "sendPhoto", chat_id=chat_id, photo=m.listing.image_urls[0],
                    caption=text, parse_mode="HTML",
                )
                return
            except Exception as exc:  # fall back to a text message
                log.info("sendPhoto failed, falling back to text: %s", exc)
        await self.send_message(chat_id, text)

    async def admin(self, text: str) -> None:
        if not (self.enabled and self.admin_chat_id):
            log.warning("ADMIN ALERT (no Telegram admin chat configured): %s", text)
            return
        try:
            await self.send_message(self.admin_chat_id, html.escape(text))
        except Exception:
            log.exception("failed to send admin alert")

    async def get_updates(self) -> list[dict[str, Any]]:
        body = await self._call("getUpdates", timeout=0, allowed_updates=["message"])
        return body.get("result") or []

    async def confirm_updates(self, last_update_id: int) -> None:
        await self._call("getUpdates", offset=last_update_id + 1, timeout=0)


async def link_telegram_accounts(tg: Telegram, store: Any) -> int:
    """Handle `/start <code>` messages: save chat IDs on matching profiles."""
    if not tg.enabled:
        return 0
    updates = await tg.get_updates()
    linked = 0
    for u in updates:
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            continue
        match = START_RE.match(text)
        if not match:
            if text.startswith("/start"):
                await _safe_send(tg, chat_id, "Send the code shown on the Sourcer settings page: /start CODE")
            continue
        ok = await store.link_telegram(match.group(1), int(chat_id))
        linked += int(ok)
        await _safe_send(
            tg, chat_id,
            "✅ Linked. New matches will be sent here." if ok
            else "That code wasn't recognised. Generate a new one on the settings page.",
        )
    if updates:
        await tg.confirm_updates(max(u["update_id"] for u in updates))
    return linked


async def _safe_send(tg: Telegram, chat_id: int, text: str) -> None:
    try:
        await tg.send_message(chat_id, html.escape(text))
    except Exception:
        log.exception("telegram reply failed")


async def send_pending(
    tg: Telegram | None,
    store: Any,
    profiles: dict[str, Profile],
    settings: Settings,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Send alerts for all un-notified matches, then set notified_at.

    * one alert per listing per user (two searches matching one listing -> one message)
    * > digest_threshold matches for a user in one run -> a single digest
    * notify_mode 'digest' -> always a digest; 'off' / no chat linked -> mark only
    """
    pending = await store.pending_matches()
    by_user: dict[str, list[PendingMatch]] = defaultdict(list)
    for m in pending:
        by_user[m.user_id].append(m)

    sent = 0
    now = datetime.now(UTC)
    for user_id, matches in by_user.items():
        # Dedupe by listing, keep the first search that matched it.
        unique: dict[str, PendingMatch] = {}
        for m in matches:
            unique.setdefault(m.listing_id, m)
        items = list(unique.values())
        profile = profiles.get(user_id)
        chat_id = profile.telegram_chat_id if profile else None
        mode = profile.notify_mode if profile else "off"

        if dry_run:
            for m in items:
                print(f"  [notify:{mode}] {user_id[:8]} <- {m.listing.title[:70]} | {m.listing.url}")
            continue

        ids_by_listing: dict[str, list[str]] = defaultdict(list)
        for m in matches:
            ids_by_listing[m.listing_id].append(m.match_id)

        if not (tg and tg.enabled and chat_id and mode != "off"):
            await store.mark_notified([m.match_id for m in matches], now)
            continue
        try:
            if mode == "digest" or len(items) > settings.digest_threshold:
                await tg.send_message(chat_id, digest(items, settings.digest_top_n))
                sent += 1
                await store.mark_notified([m.match_id for m in matches], now)
            else:
                for m in items:
                    await tg.send_match(chat_id, m)
                    sent += 1
                    # Mark right away so a later failure never causes a re-send.
                    await store.mark_notified(ids_by_listing[m.listing_id], now)
                    await asyncio.sleep(0.4)
        except Exception:
            log.exception("telegram delivery to %s failed; remaining matches retry next run", user_id)
    return {"users": len(by_user), "messages": sent}
