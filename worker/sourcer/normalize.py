"""Text normalisation shared by matching and the spam filter."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_APOSTROPHES = {ord(c): "'" for c in "’‘`´＇ʼ′"}
_YS = re.compile(r"(?<![a-z0-9])y\s?'\s?s(?![a-z0-9])")
_WS = re.compile(r"\s+")
_ASCII_TERM = re.compile(r"^[a-z0-9' .&/+-]+$")


def normalize(text: str | None) -> str:
    """NFKC (full-width -> half-width), unify apostrophes, lower-case,
    unify Y's / Ys / Y’s variants, collapse whitespace."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_APOSTROPHES).lower()
    t = _YS.sub("ys", t)
    return _WS.sub(" ", t).strip()


@lru_cache(maxsize=4096)
def _term_pattern(term_norm: str) -> re.Pattern[str]:
    # Latin terms must match on word boundaries ("ys" must not hit "days").
    # Japanese/other scripts have no spaces, so they match as substrings.
    escaped = re.escape(term_norm).replace(r"\ ", r"\s?")
    if _ASCII_TERM.match(term_norm):
        return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
    return re.compile(escaped)


def contains_term(text_norm: str, term: str) -> bool:
    """True if `term` (raw user input) appears in already-normalised text."""
    term_norm = normalize(term)
    if not term_norm:
        return False
    return _term_pattern(term_norm).search(text_norm) is not None


def parse_terms(raw: str | None) -> list[str]:
    """Split comma / newline separated user input into clean terms."""
    if not raw:
        return []
    parts = re.split(r"[,\n、，]", raw)
    return [p.strip() for p in parts if p.strip()]
