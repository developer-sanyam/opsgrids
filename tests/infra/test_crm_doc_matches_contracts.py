"""`docs/crm.md` says what the committed contracts and the route tables say.

The document holds four tables a person copies from: the service map (ports and gateway prefixes),
the event table, the permission table and the role table. They are hand-written prose around
generated facts, and a hand-written table drifts the day a release adds an event, a permission or a
route. This test is the tripwire: it fails, naming the row, when a table and its source disagree.

Sources of truth, never the other way round:

    events        libs/contracts/events/crm.*/v1.schema.json
    permissions   libs/contracts/permissions/crm.json      (`permissions`)
    roles         libs/contracts/permissions/crm.json      (`role_templates`)
    ports         services/app-gateway/routes.json         (dev port) and
                  infrastructure/edge/routes.prod.json     (the service behind each prefix)

**Changing a CRM event, permission, role or gateway prefix? Update `docs/crm.md` in the same
commit.**

The document's layout is R1.1's (`docs/superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-
design.md` section 4.5): twelve `## ` sections, with the service map, the events and the
permissions-and-roles sections found here by their headings. If a heading is renamed, the three
`SECTION_*` words below are the one place to follow it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRM_DOC = REPO_ROOT / "docs" / "crm.md"
EVENTS_DIR = REPO_ROOT / "libs" / "contracts" / "events"
CRM_MANIFEST = REPO_ROOT / "libs" / "contracts" / "permissions" / "crm.json"
DEV_ROUTES = REPO_ROOT / "services" / "app-gateway" / "routes.json"
PROD_ROUTES = REPO_ROOT / "infrastructure" / "edge" / "routes.prod.json"

#: Lower-case words that identify each section by its `## ` heading.
SECTION_SERVICES = "service map"
SECTION_EVENTS = "event"
SECTION_PERMISSIONS = "permission"

_EVENT_TYPE = re.compile(r"^crm\.(?:(?:book|pipeline)\.[a-z_]+\.[a-z_]+|lead\.[a-z_]+)$")
_PERMISSION_CODE = re.compile(r"^crm\.[a-z_]+\.[a-z_]+\.[a-z_]+$")
_ROLE_CODE = re.compile(r"^CRM_[A-Z_]+$")
_GATEWAY_PREFIX = re.compile(r"/api/v1/[a-z-]+")


def _sections() -> dict[str, list[str]]:
    """Heading text -> the lines under it, for every `## ` heading of the document."""
    assert CRM_DOC.is_file(), "docs/crm.md does not exist: R1.1 writes it, and R1.2b needs R1.1"
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in CRM_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        elif current is not None:
            current.append(line)
    return sections


def _section(word: str) -> list[str]:
    matches = {title: body for title, body in _sections().items() if word in title.lower()}
    assert len(matches) == 1, (
        f"expected exactly one `## ` heading containing {word!r} in docs/crm.md, "
        f"found {sorted(matches)} among {sorted(_sections())}"
    )
    return next(iter(matches.values()))


def _rows(lines: list[str]) -> list[list[str]]:
    """Every markdown table row under a heading as its cells, backticks and padding removed."""
    rows = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            rows.append([cell.strip().strip("`") for cell in stripped.strip("|").split("|")])
    return rows


def _first_cells(lines: list[str], pattern: re.Pattern[str]) -> set[str]:
    return {row[0] for row in _rows(lines) if row and pattern.match(row[0])}


def _delta(
    found: set[str], expected: set[str], left: str = "the document", right: str = "the contracts"
) -> str:
    return (
        f"in {left} only: {sorted(found - expected)}; in {right} only: {sorted(expected - found)}"
    )


def test_the_event_table_lists_exactly_the_crm_schemas() -> None:
    committed = {
        path.name for path in EVENTS_DIR.glob("crm.*") if (path / "v1.schema.json").is_file()
    }
    found = _first_cells(_section(SECTION_EVENTS), _EVENT_TYPE)
    assert found == committed, "docs/crm.md event table drifted: " + _delta(found, committed)


def test_the_permission_table_lists_exactly_the_manifest_codes() -> None:
    manifest = json.loads(CRM_MANIFEST.read_text(encoding="utf-8"))
    expected = {
        f"{manifest['product']}.{p['module']}.{p['resource']}.{p['action']}"
        for p in manifest["permissions"]
    }
    found = _first_cells(_section(SECTION_PERMISSIONS), _PERMISSION_CODE)
    assert found == expected, "docs/crm.md permission table drifted: " + _delta(found, expected)


def test_the_role_table_lists_exactly_the_manifest_templates() -> None:
    manifest = json.loads(CRM_MANIFEST.read_text(encoding="utf-8"))
    expected = {template["code"] for template in manifest["role_templates"]}
    found = _first_cells(_section(SECTION_PERMISSIONS), _ROLE_CODE)
    assert found == expected, "docs/crm.md role table drifted: " + _delta(found, expected)


def _crm_prefixes_by_service() -> dict[str, dict[str, str]]:
    """Service -> {gateway prefix -> dev port}: the dev table gives the port, the prod table the
    service behind the same prefix (`http://customer:8000`)."""
    dev = {
        route["prefix"]: route["upstream"].rsplit(":", 1)[1]
        for route in json.loads(DEV_ROUTES.read_text(encoding="utf-8"))["routes"]
        if route.get("product") == "crm"
    }
    prod = {
        route["prefix"]: route["upstream"].split("//", 1)[1].split(":", 1)[0]
        for route in json.loads(PROD_ROUTES.read_text(encoding="utf-8"))["routes"]
        if route.get("product") == "crm"
    }
    assert set(dev) == set(prod), (
        "the dev and prod route tables disagree on the CRM prefixes: "
        + _delta(set(dev), set(prod), "the dev table", "the prod table")
    )
    by_service: dict[str, dict[str, str]] = {}
    for prefix, port in dev.items():
        by_service.setdefault(prod[prefix], {})[prefix] = port
    return by_service


def test_the_service_map_ports_match_the_gateway() -> None:
    expected = _crm_prefixes_by_service()
    rows = {
        row[0]: " ".join(row)
        for row in _rows(_section(SECTION_SERVICES))
        if row and row[0] in expected
    }
    assert set(rows) == set(expected), (
        f"docs/crm.md service map should have one row per CRM service {sorted(expected)}, "
        f"has {sorted(rows)}"
    )
    for service, prefixes in expected.items():
        text = rows[service]
        ports = set(prefixes.values())
        assert len(ports) == 1, f"{service} is routed to more than one dev port: {sorted(ports)}"
        (port,) = ports
        assert re.search(rf"\b{port}\b", text), f"service map: {service} should name port {port}"
        mentioned = set(_GATEWAY_PREFIX.findall(text))
        assert mentioned == set(prefixes), f"service map: {service} prefixes drifted: " + _delta(
            mentioned, set(prefixes)
        )
