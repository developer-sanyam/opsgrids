"""Writes for account.

Business rules, transactions, audit rows and outbox rows. A write opens its own
transaction with `async with session.begin()` so the state change, its audit entry and
any event it emits all commit or all roll back.
"""

from __future__ import annotations
