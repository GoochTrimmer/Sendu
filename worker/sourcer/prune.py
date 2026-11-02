"""Daily storage cleanup (README 4.7). The SQL lives in prune_storage() in the migration."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


async def prune(store: Any) -> dict[str, Any]:
    result = await store.prune()
    log.info("pruned: %s", result)
    return result
