"""Helpers the demo seeders share.

Imported as a sibling (`from _common import ...`): every seeder runs as a script, so
`tools/demo/` is `sys.path[0]`.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime


def datetime_from_iso(s: str) -> datetime:
    """An ISO-8601 timestamp, with a trailing `Z` read as UTC."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def date_from_iso(s: str) -> date:
    return date.fromisoformat(s)


def bootstrap() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Parse argv and put the service dir on the import path — sync, before the event loop.

    `argv`: the service directory, then the organization, admin user and admin membership ids.
    """
    from pathlib import Path

    service_dir = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(service_dir))
    return uuid.UUID(sys.argv[2]), uuid.UUID(sys.argv[3]), uuid.UUID(sys.argv[4])
