"""platform-core — the shared platform every OpsGrids service is built on.

Import from here, not from submodules, for everything on this surface. Anything not
listed lives in its submodule on purpose: `events`, `audit`, `testing` and `locks` are
imported explicitly so their cost and intent are visible at the call site.
"""

from platform_core.app import create_app
from platform_core.auth import (
    CurrentPrincipal,
    Principal,
    Scope,
    ScopeContext,
    decode_token,
    get_principal,
    require,
    require_product,
    require_service_token,
)
from platform_core.config import ServiceSettings, get_settings, settings_for
from platform_core.db import (
    get_scoped,
    get_session,
    get_sessionmaker,
    get_tenant_session,
    init_engine,
    session_scope,
    set_tenant,
)
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    PaymentRequiredProblem,
    ProblemError,
    RateLimitedProblem,
    UnauthorizedProblem,
    ValidationProblem,
)
from platform_core.logging import get_logger
from platform_core.models import Base, TenantBase, utcnow
from platform_core.money import Money, total
from platform_core.pagination import Page, PageParams, PageQuery, paginate
from platform_core.pii import (
    NON_SENSITIVE_KINDS,
    PII_KINDS,
    PiiError,
    mask,
    open_sealed,
    seal,
)

__all__ = [
    "NON_SENSITIVE_KINDS",
    "PII_KINDS",
    "Base",
    "BusinessRuleProblem",
    "ConflictProblem",
    "CurrentPrincipal",
    "ForbiddenProblem",
    "Money",
    "NotFoundProblem",
    "Page",
    "PageParams",
    "PageQuery",
    "PaymentRequiredProblem",
    "PiiError",
    "Principal",
    "ProblemError",
    "RateLimitedProblem",
    "Scope",
    "ScopeContext",
    "ServiceSettings",
    "TenantBase",
    "UnauthorizedProblem",
    "ValidationProblem",
    "create_app",
    "decode_token",
    "get_logger",
    "get_principal",
    "get_scoped",
    "get_session",
    "get_sessionmaker",
    "get_settings",
    "get_tenant_session",
    "init_engine",
    "mask",
    "open_sealed",
    "paginate",
    "require",
    "require_product",
    "require_service_token",
    "seal",
    "session_scope",
    "set_tenant",
    "settings_for",
    "total",
    "utcnow",
]
