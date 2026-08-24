"""Reads for quotation. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)`. A list endpoint that queries directly instead
of coming through a selector is how an organization leaks.
"""

from __future__ import annotations
