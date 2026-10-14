"""The ITSM permission catalogue and role templates, for the `itsm` product.

Mirrors account- and employee-service's catalogue, and for the same reason it is shaped the way
it is: in the system this platform replaces, an admin role was "every permission that exists",
so the moment another product registered its permissions the admin silently held them too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's permission
  without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit. A product's admin role is built from that product's own
catalogue, never "all permissions" (CLAUDE.md).
"""

from __future__ import annotations

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "itsm"


# --------------------------------------------------------------------- itsm catalogue

ITSM = PermissionCatalogue(
    PRODUCT,
    [
        # Incidents. Reading has a meaningful OWN reading (the incidents I raised), so it
        # defaults to OWN and tops out at ORG. Writing (raise, update, add a worklog) has an
        # OWN reading too — a user can raise and comment on their own ticket — so the write
        # routes set no floor and the object-level check does the rest.
        PermissionSpec("service", "incident", "read", "See incidents and their worklog", "OWN"),
        PermissionSpec(
            "service", "incident", "write", "Raise incidents, update them and add worklog", "OWN"
        ),
        # Service requests and the catalog they draw from. Catalog reads ride on request.read
        # because the catalog is the request surface's reference data.
        PermissionSpec("service", "request", "read", "See service requests and the catalog", "OWN"),
        PermissionSpec("service", "request", "write", "Raise and fulfil service requests", "OWN"),
        # The catalog itself is the organization's reference data, not anyone's own row: an
        # offering has no OWN reading, so writing it floors at ORG (the route carries the
        # matching `minimum=Scope.ORG`) while reading it still rides on request.read.
        PermissionSpec(
            "service", "catalog", "write", "Add and change catalog offerings", "ORG", "ORG"
        ),
        # CMDB. Assets are org infrastructure, so the natural reading is ORG for an agent; the
        # permission still tops out at ORG and OWN reads the assets a person owns.
        PermissionSpec("cmdb", "asset", "read", "See CMDB assets", "OWN"),
        PermissionSpec("cmdb", "asset", "write", "Create and update CMDB assets", "OWN"),
    ],
)


ITSM_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="ITSM_ADMIN",
        name="Service desk administrator",
        product=PRODUCT,
        description=(
            "Administers the whole service desk and CMDB across the organization. Built from the "
            "ITSM catalogue alone — administering IT is not the same job as owning the account."
        ),
        grants=ITSM.all("ORG"),
    ),
    RoleTemplate(
        code="ITSM_AGENT",
        name="Service desk agent",
        product=PRODUCT,
        description=(
            "Works the incident and request queues org-wide and reads the CMDB, but does not "
            "administer assets or the service catalog."
        ),
        grants=ITSM.pick(
            ("itsm.service.incident.read", "ORG"),
            ("itsm.service.incident.write", "ORG"),
            ("itsm.service.request.read", "ORG"),
            ("itsm.service.request.write", "ORG"),
            ("itsm.cmdb.asset.read", "ORG"),
        ),
    ),
    RoleTemplate(
        code="ITSM_USER",
        name="ITSM user",
        product=PRODUCT,
        description="Every person with an ITSM seat. Sees and raises their own tickets.",
        is_default=True,
        grants=ITSM.pick(
            ("itsm.service.incident.read", "OWN"),
            ("itsm.service.incident.write", "OWN"),
            ("itsm.service.request.read", "OWN"),
            ("itsm.service.request.write", "OWN"),
            ("itsm.cmdb.asset.read", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(ITSM_ROLE_TEMPLATES)


ITSM_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=ITSM, role_templates=ITSM_ROLE_TEMPLATES
)
