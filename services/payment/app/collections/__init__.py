"""Tenant payment collection (ADR-0024 §8, decision A4): a tenant's own customers paying the
tenant, into the tenant's own Razorpay account — `payee = 'merchant'`.

The same service, tables and provider seam as platform billing, and the opposite direction. The
module keeps the standard anatomy (`domain` pure, `selectors` reads, `services` writes, `cron`
the expiry sweep); its HTTP surfaces are `app/api/collections.py` (the tenant admin screens and
the merchant webhook) and `app/api/collections_internal.py` (what operations calls).
"""
