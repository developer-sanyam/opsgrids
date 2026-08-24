"""Writes for support.

Business rules, audit rows and outbox rows. Nothing here opens or commits a transaction:
a route with a principal takes its session from `get_tenant_session`, which opens the
request's one transaction and binds it to the caller's organization first (ADR-0002 §1),
so the state change, its audit entry and any event it emits all commit or all roll back
together.
"""

from __future__ import annotations
