"""Matching pipeline (README 4.5): blocklist, include, exclude, spam, price, size.

Pure functions only; easy to unit test.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA_DIR, convert
from .models import BlockedSeller, Listing, SavedSearch
from .normalize import contains_term, normalize

# Reasons a listing can be dropped, in pipeline order.
BLOCKED = "blocked"
INCLUDE = "include"
EXCLUDED = "excluded"
SPAM = "spam"
PRICE = "price"
SIZE = "size"


@dataclass(frozen=True)
class Brand:
    name: str
    aliases: tuple[str, ...]


def load_brands(path: Path | None = None) -> list[Brand]:
    """brands.txt: one brand family per line, aliases separated by '|'. '#' = comment."""
    path = path or DATA_DIR / "brands.txt"
    brands: list[Brand] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        aliases = tuple(a.strip() for a in line.split("|") if a.strip())
        if aliases:
            brands.append(Brand(aliases[0], aliases))
    return brands


def count_brands(text_norm: str, brands: Iterable[Brand]) -> int:
    """Number of distinct brand families mentioned in normalised text."""
    return sum(1 for b in brands if any(contains_term(text_norm, a) for a in b.aliases))


class Blocklist:
    """Shared + personal blocked sellers. Matches on seller ID.

    An entry whose seller_id starts with 'name:' matches the seller's username instead.
    That's used when someone pastes a profile URL that only contains a username
    (e.g. Grailed); blocking from a feed card always stores the real ID.
    """

    def __init__(self, entries: Iterable[BlockedSeller] = ()):
        self._shared_ids: set[tuple[str, str]] = set()
        self._shared_names: set[tuple[str, str]] = set()
        self._personal_ids: dict[str, set[tuple[str, str]]] = {}
        self._personal_names: dict[str, set[tuple[str, str]]] = {}
        for e in entries:
            self.add(e)

    def add(self, e: BlockedSeller) -> None:
        is_name = e.seller_id.startswith("name:")
        key = (e.marketplace, e.seller_id[5:].lower() if is_name else e.seller_id)
        if e.scope == "shared":
            (self._shared_names if is_name else self._shared_ids).add(key)
        elif e.owner_id:
            target = self._personal_names if is_name else self._personal_ids
            target.setdefault(e.owner_id, set()).add(key)

    def is_blocked(self, user_id: str, listing: Listing) -> bool:
        mp = listing.marketplace
        if listing.seller_id:
            k = (mp, listing.seller_id)
            if k in self._shared_ids or k in self._personal_ids.get(user_id, ()):
                return True
        if listing.seller_name:
            k = (mp, listing.seller_name.lower())
            if k in self._shared_names or k in self._personal_names.get(user_id, ()):
                return True
        return False


def _size_matches(listing_size: str | None, wanted: list[str]) -> bool:
    if not wanted:
        return True
    if not listing_size:
        # Many marketplaces don't return a size in search results. Keep the
        # listing rather than silently losing it.
        return True
    size_norm = normalize(listing_size)
    return any(
        normalize(w) == size_norm or contains_term(size_norm, w) for w in wanted if w.strip()
    )


@dataclass
class Matcher:
    brands: list[Brand]
    blocklist: Blocklist = field(default_factory=Blocklist)
    spam_threshold: int = 3

    def evaluate(self, listing: Listing, search: SavedSearch) -> str | None:
        """Return the reason the listing is dropped, or None if it matches."""
        mp = listing.marketplace
        text = normalize(f"{listing.title} {listing.description or ''} {listing.brand or ''}")

        if self.blocklist.is_blocked(search.user_id, listing):
            return BLOCKED

        if any(not contains_term(text, t) for t in search.include_terms if t.strip()):
            return INCLUDE

        if search.exclude_enabled_for(mp) and any(
            contains_term(text, t) for t in search.exclude_terms_for(mp) if t.strip()
        ):
            return EXCLUDED

        if search.spam_filter_enabled:
            # Only title + description: the structured brand field is a single tag.
            body = normalize(f"{listing.title} {listing.description or ''}")
            if count_brands(body, self.brands) >= self.spam_threshold:
                return SPAM

        if search.min_price is not None or search.max_price is not None:
            price = convert(listing.price, listing.currency, search.currency)
            if price is not None:
                if search.min_price is not None and price < search.min_price:
                    return PRICE
                if search.max_price is not None and price > search.max_price:
                    return PRICE

        if not _size_matches(listing.size, search.sizes):
            return SIZE

        return None


def new_stats() -> Counter[str]:
    c: Counter[str] = Counter()
    for k in ("fetched", "new", "matched", BLOCKED, INCLUDE, EXCLUDED, SPAM, PRICE, SIZE):
        c[k] = 0
    return c
