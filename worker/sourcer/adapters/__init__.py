from __future__ import annotations

from ..config import Settings
from .base import AdapterNotConfigured, MarketplaceAdapter
from .ebay import EbayAdapter
from .grailed import GrailedAdapter
from .mercari_jp import MercariJPAdapter
from .yahoo_auctions import YahooAuctionsAdapter

ALL_CODES = ("ebay", "grailed", "mercari_jp", "yahoo_auctions")


def build_adapters(settings: Settings) -> dict[str, MarketplaceAdapter]:
    return {
        "ebay": EbayAdapter(settings),
        "grailed": GrailedAdapter(settings),
        "mercari_jp": MercariJPAdapter(),
        "yahoo_auctions": YahooAuctionsAdapter(),
    }


__all__ = ["ALL_CODES", "AdapterNotConfigured", "MarketplaceAdapter", "build_adapters"]
