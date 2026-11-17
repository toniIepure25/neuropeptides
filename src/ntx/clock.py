"""Single source of time, so a run can be made deterministic.

Set ``NTX_FROZEN_TIME`` to an ISO-8601 instant (or ``1`` for the epoch) and every
timestamp in reports, manifests and ingested records uses that instant. This is
what allows ``ntx demo --frozen-time`` to produce byte-identical output.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

FROZEN_ENV = "NTX_FROZEN_TIME"
DEFAULT_FROZEN = "1970-01-01T00:00:00+00:00"


def is_frozen() -> bool:
    return bool(os.environ.get(FROZEN_ENV))


def now() -> datetime:
    raw = os.environ.get(FROZEN_ENV)
    if raw:
        value = raw.strip()
        if value.lower() in {"1", "true", "yes"}:
            value = DEFAULT_FROZEN
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(UTC)


def iso() -> str:
    return now().isoformat(timespec="seconds")
