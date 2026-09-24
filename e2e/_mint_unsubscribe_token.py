"""Mint a real, validly-signed Engage unsubscribe token. Run **inside `services/notification`**
as a subprocess — see `_seed_crm.py`'s docstring for why this cannot be a plain import: every
service exposes a top-level `app`, so one interpreter can only ever hold one of them.

Usage: _mint_unsubscribe_token.py <service_dir> <org_id> <email>

Prints the token to stdout. v1 (`services.unsubscribe_token`) rather than v2 deliberately:
resolving a v1 link (`services.unsubscribe`) needs no existing contact or campaign recipient
row, so minting one needs no campaign to have actually been sent first. The edge smoke test
this feeds is proving something about the *gateway* — that it lets an unauthenticated request
through to notification-service at all — not re-proving the full send pipeline `test_engage_
journey.py` already cannot exercise end to end (real DNS never verifies in this environment).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
ORG_ID = uuid.UUID(sys.argv[2])
EMAIL = sys.argv[3]

sys.path.insert(0, str(SERVICE_DIR))

from app import services  # noqa: E402

print(services.unsubscribe_token(ORG_ID, EMAIL))
