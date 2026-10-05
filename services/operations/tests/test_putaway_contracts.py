"""The contracts storage put-away adds, and the static guards that keep a worker's coordinates
behind the one door the owner approved (decision D3, 2026-10-05).

* **Catalogue** — `operations.inventory.putaway.location_read` is four segments, ORG-only,
  dangerous, and held by the administrator role alone; the committed permission manifest agrees.
* **OpenAPI** — every new route is documented the way it behaves (the required, bounded
  `Idempotency-Key`; the `warnings` field; closed request bodies; integer — never float —
  coordinates); the committed document carries the same new surface the code builds; and a walk of
  every operation's request and response schemas proves a worker's coordinates are reachable from
  exactly two operations: the inbound put-away body and the audited reveal's response.
* **Event schema** — `operations.inventory.putaway.recorded` is closed (a coordinate in the
  payload is a schema violation), mirrors `stock.transferred`'s conventions, leaves it alone.
* **Source** — the service's code names a coordinate only where it should, never passes one (or a
  caught error's text) to a log call, and carries no Maps key or Maps URL.

The full-document equality of `libs/contracts/openapi/operations.json` and `export_permissions.py`
are the repository gates (`make openapi-check`); this file checks the put-away surface itself.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError
from jsonschema import validate as json_validate

from app.catalogue import OPERATIONS, OPERATIONS_ROLE_TEMPLATES
from app.main import app
from platform_core.events import event_schema_path
from tests.putaway_fixtures import LAT, LNG, coordinate_leaks

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "services" / "operations"
CONTRACTS = ROOT / "libs" / "contracts"
CODE = "operations.inventory.putaway.location_read"
PUTAWAY_EVENT = "operations.inventory.putaway.recorded"
TRANSFER_EVENT = "operations.inventory.stock.transferred"


# ------------------------------------------------------------------ catalogue


def _spec():  # type: ignore[no-untyped-def]
    return next(s for s in OPERATIONS.specs if s.code(OPERATIONS.product) == CODE)


class TestCatalogue:
    def test_the_code_is_four_segments_in_the_catalogue_grammar(self) -> None:
        assert len(CODE.split(".")) == 4
        assert re.fullmatch(r"[a-z]+\.[a-z]+\.[a-z]+\.[a-z_]+", CODE)
        assert CODE in OPERATIONS.codes()
        # The module / resource / action it is declared under spell the same code.
        spec = _spec()
        assert (spec.module, spec.resource, spec.action) == (
            "inventory",
            "putaway",
            "location_read",
        )

    def test_it_is_org_only_and_dangerous(self) -> None:
        spec = _spec()
        assert spec.max_scope == "ORG" and spec.default_scope == "ORG"
        assert spec.is_dangerous is True
        assert "audited" in spec.description

    def test_only_the_administrator_role_holds_it(self) -> None:
        holders = {
            t.code: {g.code: g.scope for g in t.grants}.get(CODE) for t in OPERATIONS_ROLE_TEMPLATES
        }
        assert holders == {
            "OPERATIONS_ADMIN": "ORG",
            "OPERATIONS_MANAGER": None,
            "OPERATIONS_WORKER": None,
            "OPERATIONS_USER": None,
        }

    def test_a_worker_keeps_what_it_always_had_and_gains_no_way_to_see_positions(self) -> None:
        worker = {
            g.code
            for t in OPERATIONS_ROLE_TEMPLATES
            if t.code == "OPERATIONS_WORKER"
            for g in t.grants
        }
        assert {"operations.inventory.stock.transfer", "operations.inventory.stock.read"} <= worker
        assert not any("location_read" in code for code in worker)
        assert not any(code.startswith("operations.inventory.putaway") for code in worker)

    def test_the_committed_manifest_carries_it_with_the_same_flags(self) -> None:
        manifest = json.loads((CONTRACTS / "permissions" / "operations.json").read_text("utf-8"))
        [entry] = [
            p
            for p in manifest["permissions"]
            if f"{manifest['product']}.{p['module']}.{p['resource']}.{p['action']}" == CODE
        ]
        assert entry["is_dangerous"] is True
        assert (entry["max_scope"], entry["default_scope"]) == ("ORG", "ORG")
        grants = {
            t["code"]: [g["scope"] for g in t["grants"] if g["code"] == CODE]
            for t in manifest["role_templates"]
        }
        assert grants == {
            "OPERATIONS_ADMIN": ["ORG"],
            "OPERATIONS_MANAGER": [],
            "OPERATIONS_WORKER": [],
            "OPERATIONS_USER": [],
        }

    def test_the_manifest_has_one_entry_per_code_in_the_catalogue(self) -> None:
        manifest = json.loads((CONTRACTS / "permissions" / "operations.json").read_text("utf-8"))
        committed = {
            f"{manifest['product']}.{p['module']}.{p['resource']}.{p['action']}"
            for p in manifest["permissions"]
        }
        assert committed == set(OPERATIONS.codes())


# ------------------------------------------------------------------ OpenAPI

LIVE: dict[str, Any] = app.openapi()
COMMITTED: dict[str, Any] = json.loads(
    (CONTRACTS / "openapi" / "operations.json").read_text("utf-8")
)
P = "/api/v1/operations"
NEW_OPERATIONS = {
    ("post", f"{P}/inventory/putaway"),
    ("get", f"{P}/inventory/putaway/suggest"),
    ("get", f"{P}/inventory/putaway/{{move_id}}"),
    ("post", f"{P}/inventory/putaway/{{move_id}}/location:reveal"),
    ("get", f"{P}/inventory/locate"),
    ("get", f"{P}/floors/{{floor_id}}/published-layout"),
    ("get", f"{P}/warehouses/map"),
}
NEW_SCHEMAS = (
    "PutawayIn",
    "PutawayOut",
    "PutawayFromOut",
    "PutawayToOut",
    "PutawayDetailOut",
    "PutawayLocationOut",
    "PutawaySuggestOut",
    "PutawaySuggestionOut",
    "LocateOut",
    "LocateRowOut",
    "LocatePlacementOut",
    "PublishedLayoutOut",
    "PublishedPlacementOut",
    "WarehouseMapOut",
    "WarehouseMapSiteOut",
    "WarehouseMapUnplacedOut",
    "WarehouseIn",
    "WarehouseOut",
    "WarehousePatch",
)
COORDINATE_PROPERTY = re.compile(r"^(lat|lng)_micro$|^accuracy_m$")


def _resolve(ref: str, doc: dict[str, Any]) -> dict[str, Any]:
    node: Any = doc
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return node  # type: ignore[no-any-return]


def _schema_names(node: Any, doc: dict[str, Any], seen: set[str] | None = None) -> set[str]:
    """Every component schema reachable from `node` through `$ref`s."""
    seen = set() if seen is None else seen
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                _schema_names(_resolve(node["$ref"], doc), doc, seen)
        for value in node.values():
            _schema_names(value, doc, seen)
    elif isinstance(node, list):
        for value in node:
            _schema_names(value, doc, seen)
    return seen


def _operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, item in LIVE["paths"].items()
        for method, op in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def _bodies(op: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for content in (op.get("requestBody") or {}).get("content", {}).values():
        out.append(content.get("schema", {}))
    return out


def _responses(op: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for response in op.get("responses", {}).values():
        for content in response.get("content", {}).values():
            out.append(content.get("schema", {}))
    return out


def _carries_coordinates(schema: dict[str, Any]) -> bool:
    return any(COORDINATE_PROPERTY.match(name) for name in schema.get("properties", {}))


class TestOpenApi:
    def test_every_new_route_is_documented(self) -> None:
        documented = {(m, p) for m, p, _ in _operations()}
        assert documented >= NEW_OPERATIONS

    def test_the_put_away_post_documents_a_required_bounded_key_and_its_201(self) -> None:
        op = LIVE["paths"][f"{P}/inventory/putaway"]["post"]
        [key] = [p for p in op["parameters"] if p["name"] == "Idempotency-Key"]
        assert key["in"] == "header" and key["required"] is True
        assert key["schema"]["minLength"] == 1 and key["schema"]["maxLength"] == 120
        assert op["responses"]["201"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/PutawayOut"
        }
        assert op["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/PutawayIn"
        }

    def test_the_receipt_declares_its_warnings(self) -> None:
        out = LIVE["components"]["schemas"]["PutawayOut"]
        assert out["properties"]["warnings"] == {
            "items": {"type": "string"},
            "type": "array",
            "title": "Warnings",
        }
        assert "warnings" in out["required"]
        assert set(out["properties"]) == {
            "move_id",
            "replayed",
            "from_location",
            "to_location",
            "matched_suggestion",
            "geo_recorded",
            "warnings",
        }

    def test_the_suggestion_declares_the_bins_own_barcode_as_a_required_nullable_string(
        self,
    ) -> None:
        """A worker holds no `location.read`: the suggestion's `barcode` is how the device matches
        a scanned label. Required (the key is always present) and nullable (a bin may have none)."""
        suggestion = LIVE["components"]["schemas"]["PutawaySuggestionOut"]
        assert suggestion["properties"]["barcode"]["anyOf"] == [
            {"type": "string"},
            {"type": "null"},
        ]
        assert "barcode" in suggestion["required"]
        assert set(suggestion["properties"]) == {
            "location_id",
            "code",
            "barcode",
            "name",
            "path",
            "reason",
        }

    def test_the_inbound_body_documents_that_an_unresolvable_suggestion_is_ignored(self) -> None:
        body = LIVE["components"]["schemas"]["PutawayIn"]
        assert "suggestion_ignored" in body["description"]
        assert "suggestion_ignored" in LIVE["components"]["schemas"]["PutawayOut"]["description"]

    def test_a_movements_reference_type_is_any_string_not_a_single_literal(self) -> None:
        """`reference_type` is a free column (`purchase_order`, `bin_transfer`, `putaway`, ...): a
        `const` in the schema would make a generated client reject a legitimate row."""
        prop = LIVE["components"]["schemas"]["StockMovementOut"]["properties"]["reference_type"]
        assert prop["anyOf"] == [{"type": "string"}, {"type": "null"}]

    def test_the_inbound_body_is_closed_and_the_fix_is_integers(self) -> None:
        body = LIVE["components"]["schemas"]["PutawayIn"]
        assert body["additionalProperties"] is False
        for name in ("lat_micro", "lng_micro", "accuracy_m"):
            assert body["properties"][name]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
        assert "status" not in body["properties"]  # a put-away is always available stock
        assert set(body["required"]) == {
            "inventory_item_id",
            "from_location_id",
            "to_barcode",
            "quantity",
        }
        assert body["properties"]["quantity"]["type"] == "string"  # a decimal string, never a float

    def test_no_coordinate_anywhere_in_the_document_is_a_float(self) -> None:
        checked = 0
        for name, schema in LIVE["components"]["schemas"].items():
            for prop, definition in schema.get("properties", {}).items():
                if COORDINATE_PROPERTY.match(prop):
                    types = {t.get("type") for t in definition.get("anyOf", [definition])}
                    assert types <= {"integer", "null"}, (name, prop, types)
                    checked += 1
        assert checked >= 10

    def test_the_geometry_of_a_floor_is_integer_millimetres(self) -> None:
        for name in ("LocatePlacementOut", "PublishedPlacementOut"):
            for prop, definition in LIVE["components"]["schemas"][name]["properties"].items():
                if prop.endswith("_mm"):
                    types = {t.get("type") for t in definition.get("anyOf", [definition])}
                    assert types <= {"integer", "null"}, (name, prop)

    def test_the_committed_document_carries_the_same_new_surface_as_the_code(self) -> None:
        for method, path in NEW_OPERATIONS:
            assert COMMITTED["paths"][path][method] == LIVE["paths"][path][method], (method, path)
        for name in NEW_SCHEMAS:
            assert (
                COMMITTED["components"]["schemas"][name] == LIVE["components"]["schemas"][name]
            ), name

    def test_a_workers_coordinates_are_reachable_from_exactly_two_operations(self) -> None:
        """The walk: for every operation, every schema reachable from its request body or any of
        its responses. `PutawayIn` (the device's fix, inbound) and `PutawayLocationOut` (the
        audited reveal) are the only worker-position models, and each is used by one operation."""
        uses: dict[str, set[tuple[str, str, str]]] = {}
        for method, path, op in _operations():
            for side, schemas in (("request", _bodies(op)), ("response", _responses(op))):
                for schema in schemas:
                    for name in _schema_names(schema, LIVE):
                        uses.setdefault(name, set()).add((method, path, side))
        assert uses["PutawayIn"] == {("post", f"{P}/inventory/putaway", "request")}
        assert uses["PutawayLocationOut"] == {
            ("post", f"{P}/inventory/putaway/{{move_id}}/location:reveal", "response")
        }

    def test_the_only_models_that_carry_a_position_are_the_two_worker_ones_and_the_site_ones(
        self,
    ) -> None:
        carriers = {
            name
            for name, schema in LIVE["components"]["schemas"].items()
            if _carries_coordinates(schema)
        }
        assert carriers == {
            "PutawayIn",
            "PutawayLocationOut",
            # A warehouse's own position on a map — a business address (approved, spec 4.8).
            "WarehouseIn",
            "WarehouseOut",
            "WarehousePatch",
            "WarehouseMapSiteOut",
        }

    def test_no_other_response_reaches_a_model_that_carries_a_workers_position(self) -> None:
        worker_models = {"PutawayIn", "PutawayLocationOut"}
        leaks = []
        for method, path, op in _operations():
            reached: set[str] = set()
            for schema in _responses(op):
                reached |= _schema_names(schema, LIVE)
            if (reached & worker_models) and not path.endswith("/location:reveal"):
                leaks.append((method, path, reached & worker_models))
        assert leaks == []

    def test_accuracy_belongs_to_the_worker_models_alone(self) -> None:
        holders = {
            name
            for name, schema in LIVE["components"]["schemas"].items()
            if "accuracy_m" in schema.get("properties", {})
        }
        assert holders == {"PutawayIn", "PutawayLocationOut"}

    def test_the_locate_and_layout_enums_are_the_ones_the_viewer_branches_on(self) -> None:
        schemas = LIVE["components"]["schemas"]
        reason = schemas["LocateRowOut"]["properties"]["placement_reason"]
        values = {v for branch in reason["anyOf"] for v in branch.get("enum", [])}
        assert values == {"unplaced", "no_published_layout", "no_permission"}
        source = schemas["LocatePlacementOut"]["properties"]["source"]
        assert source["enum"] == ["own", "ancestor"]
        suggestion = schemas["PutawaySuggestionOut"]["properties"]["reason"]
        assert suggestion["enum"] == ["holds_item", "empty_bin"]


# ------------------------------------------------------------------ event schema


def _schema(event: str) -> dict[str, Any]:
    path = event_schema_path(event, 1, contracts_root=CONTRACTS)
    return json.loads(path.read_text("utf-8"))  # type: ignore[no-any-return]


VALID_PAYLOAD = {
    "organization_id": "7d2b3a1e-6f0c-4a63-9b8e-7a1f6c2d9e41",
    "move_id": "0c3a5b1e-1d2c-4e3f-8a9b-0123456789ab",
    "inventory_item_id": "1c3a5b1e-1d2c-4e3f-8a9b-0123456789ab",
    "from_location_id": "2c3a5b1e-1d2c-4e3f-8a9b-0123456789ab",
    "to_location_id": "3c3a5b1e-1d2c-4e3f-8a9b-0123456789ab",
    "status": "AVAILABLE",
    "lot_id": None,
    "roll_id": None,
    "quantity": "5",
    "matched_suggestion": True,
    "has_location": False,
}


class TestEventSchema:
    def test_the_schema_is_valid_json_schema_and_where_schema_uri_resolves_it(self) -> None:
        schema = _schema(PUTAWAY_EVENT)
        Draft202012Validator.check_schema(schema)
        assert schema["$id"] == f"opsgrids://contracts/{PUTAWAY_EVENT}/v1"

    def test_a_well_formed_payload_validates(self) -> None:
        json_validate(VALID_PAYLOAD, _schema(PUTAWAY_EVENT), cls=Draft202012Validator)

    @pytest.mark.parametrize("flag", [True, False])
    @pytest.mark.parametrize("matched", [True, False, None])
    def test_the_two_flags_take_their_values(self, flag: bool, matched: bool | None) -> None:
        json_validate(
            {**VALID_PAYLOAD, "has_location": flag, "matched_suggestion": matched},
            _schema(PUTAWAY_EVENT),
            cls=Draft202012Validator,
        )

    @pytest.mark.parametrize(
        "extra",
        [
            {"lat_micro": 12_971_600},
            {"lng_micro": 77_594_600},
            {"accuracy_m": 8},
            {"latitude": 12.97},
            {"longitude": 77.59},
            {"coordinates": {"lat": 1, "lng": 2}},
            {"captured_at": "2026-10-05T10:00:00Z"},
            {"worker_id": "4c3a5b1e-1d2c-4e3f-8a9b-0123456789ab"},
        ],
    )
    def test_the_schema_is_closed_so_a_coordinate_in_the_payload_is_a_violation(
        self, extra: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError):
            json_validate(
                {**VALID_PAYLOAD, **extra}, _schema(PUTAWAY_EVENT), cls=Draft202012Validator
            )

    @pytest.mark.parametrize("missing", sorted(VALID_PAYLOAD))
    def test_every_field_is_required(self, missing: str) -> None:
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != missing}
        with pytest.raises(ValidationError):
            json_validate(payload, _schema(PUTAWAY_EVENT), cls=Draft202012Validator)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("has_location", "yes"),
            ("has_location", None),
            ("matched_suggestion", "true"),
            ("quantity", 5),
            ("quantity", "5.1234"),
            ("quantity", "five"),
            ("move_id", "not-a-uuid"),
        ],
    )
    def test_a_wrongly_typed_field_is_a_violation(self, field: str, value: Any) -> None:
        schema = _schema(PUTAWAY_EVENT)
        from jsonschema import FormatChecker

        with pytest.raises(ValidationError):
            json_validate(
                {**VALID_PAYLOAD, field: value},
                schema,
                cls=Draft202012Validator,
                format_checker=FormatChecker(),
            )

    def test_it_mirrors_stock_transferred_and_adds_exactly_the_two_flags(self) -> None:
        putaway, transferred = _schema(PUTAWAY_EVENT), _schema(TRANSFER_EVENT)
        added = set(putaway["properties"]) - set(transferred["properties"])
        assert added == {"matched_suggestion", "has_location"}
        for name, definition in transferred["properties"].items():
            assert putaway["properties"][name] == definition, name
        assert set(putaway["required"]) - set(transferred["required"]) == added
        assert (
            putaway["additionalProperties"] is False
            and transferred["additionalProperties"] is False
        )

    def test_stock_transferred_is_unchanged_nine_keys_and_closed(self) -> None:
        """The bin-transfer event is a contract other consumers hold: still exactly these nine."""
        schema = _schema(TRANSFER_EVENT)
        assert schema["additionalProperties"] is False
        assert (
            set(schema["properties"])
            == set(schema["required"])
            == {
                "organization_id",
                "move_id",
                "inventory_item_id",
                "from_location_id",
                "to_location_id",
                "status",
                "lot_id",
                "roll_id",
                "quantity",
            }
        )
        assert "matched_suggestion" not in schema["properties"]
        assert "has_location" not in schema["properties"]


# ------------------------------------------------------------------ source guards

APP = SERVICE / "app"
#: Where the service's own code may name a position: the worker's fix (the capture, its validation,
#: the one reveal) and a warehouse's own site coordinates (create / patch / read / map).
MAY_NAME_A_COORDINATE = {
    # the worker's fix
    "models.py",
    "domain.py",
    "inventory/putaway_schemas.py",
    "inventory/putaway_selectors.py",
    "inventory/putaway_services.py",
    "inventory/stock_ops.py",
    # a warehouse's site
    "schemas.py",
    "api/routes.py",
    "inventory/selectors.py",
    "inventory/services.py",
}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "bind"}
COORDINATE_WORD = re.compile(
    r"(^|_)(lat|lng|lon)(_|$)|latitude|longitude|accuracy|coord|geo_fix|(^|_)fix($|_)", re.I
)


def _app_files() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def _log_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
        ):
            target = node.func.value
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name in {"log", "logger", "_log"}:
                calls.append(node)
    return calls


def log_offenders(source: str) -> list[str]:
    """Where, in `source`, a log call names a coordinate or an accuracy, or logs a caught
    exception other than as its class (`type(exc).__name__`) — a database error's text can quote
    the row it refused, coordinates included."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found: list[str] = []
    for call in _log_calls(tree):
        for node in ast.walk(call):
            names = []
            if isinstance(node, ast.keyword) and node.arg:
                names.append(node.arg)
            if isinstance(node, ast.Name):
                names.append(node.id)
            if isinstance(node, ast.Attribute):
                names.append(node.attr)
            found += [
                f"{call.lineno} names {name!r}" for name in names if COORDINATE_WORD.search(name)
            ]
            if isinstance(node, ast.Name) and node.id in {"exc", "err", "e", "ex"}:
                parent = parents.get(node)
                if not (
                    isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Name)
                    and parent.func.id == "type"
                ):
                    found.append(f"{call.lineno} logs the exception itself")
    return found


class TestTheGuardsCanFail:
    """A scan that never fires proves nothing: each guard here is shown a violation first."""

    @pytest.mark.parametrize(
        "line",
        [
            'log.info("x", lat_micro=lat_micro)',
            'log.warning("x", move_id=1, lng=fix.lng_micro)',
            'logger.error("x", accuracy_m=7)',
            'log.error("x", error=str(exc))',
            'log.error("x", error=exc)',
            'log.exception("x", detail=f"{exc}")',
            'log.info("x", coordinates=c)',
        ],
    )
    def test_the_log_walker_flags_a_coordinate_or_an_error_text(self, line: str) -> None:
        assert log_offenders(line), line

    @pytest.mark.parametrize(
        "line",
        [
            'log.info("putaway.replayed", move_id=str(move.id))',
            'log.warning("putaway.geo_dropped", move_id=m, reason=dropped)',
            'log.error("putaway.capture_failed", move_id=m, error=type(exc).__name__)',
            'log.info("putaway.location_revealed", actor_id=str(a), move_id=str(m))',
            'print("latency", latency_ms)',  # not a log call; and `latency` is not `lat`
            'log.info("x", latency_ms=3, template="t", relation=r)',
        ],
    )
    def test_the_log_walker_passes_the_lines_the_service_writes(self, line: str) -> None:
        assert log_offenders(line) == [], line

    def test_the_coordinate_scan_finds_a_key_a_number_or_a_string_that_holds_one(self) -> None:
        assert coordinate_leaks({"lat_micro": 1})
        assert coordinate_leaks({"a": [{"b": {"Longitude": 2}}]})
        assert coordinate_leaks({"accuracy_m": 9})
        assert coordinate_leaks({"note": f"at {LAT} by {LNG}"})
        assert coordinate_leaks([LAT])
        assert coordinate_leaks({"deg": f"{LAT / 1e6:.6f}"})
        assert coordinate_leaks({"x": {"y": [str(LNG)]}})

    def test_the_coordinate_scan_passes_what_the_audit_rows_legitimately_hold(self) -> None:
        assert coordinate_leaks(None) == []
        # The *names* a reveal discloses are values in a `fields` list, not keys.
        assert coordinate_leaks({"fields": ["lat_micro", "lng_micro", "accuracy_m"]}) == []
        assert coordinate_leaks({"quantity": "5", "matched_suggestion": True, "n": 12}) == []
        assert coordinate_leaks({"move_id": "0c3a5b1e-1d2c-4e3f-8a9b-0123456789ab"}) == []


class TestSourceGuards:
    def test_only_the_files_that_should_name_a_coordinate_do(self) -> None:
        mentioning = {
            str(p.relative_to(APP))
            for p in _app_files()
            if "lat_micro" in p.read_text("utf-8") or "lng_micro" in p.read_text("utf-8")
        }
        assert mentioning <= MAY_NAME_A_COORDINATE, sorted(mentioning - MAY_NAME_A_COORDINATE)
        assert {
            "models.py",
            "inventory/putaway_services.py",
            "inventory/stock_ops.py",
        } <= mentioning

    def test_the_services_log_calls_exist_to_be_checked(self) -> None:
        total = sum(len(_log_calls(ast.parse(p.read_text("utf-8")))) for p in _app_files())
        assert total > 10  # guards against the walker silently matching nothing

    def test_no_log_call_passes_a_coordinate_an_accuracy_or_a_caught_errors_text(self) -> None:
        offenders: list[str] = []
        for path in _app_files():
            offenders += [
                f"{path.relative_to(APP)}:{where}"
                for where in log_offenders(path.read_text("utf-8"))
            ]
        assert offenders == []

    def test_the_service_carries_no_maps_key_and_no_maps_url(self) -> None:
        """The key lives in the web build, referrer-restricted in Google's console; the backend has
        no business with it (and a key in source is a leaked key)."""
        pattern = re.compile(
            r"GOOGLE_MAPS|maps\.googleapis|AIza[0-9A-Za-z_-]{20,}|google\.com/maps", re.I
        )
        hits = [
            f"{p.relative_to(ROOT)}:{i}"
            for p in [*_app_files(), *(SERVICE / "migrations").rglob("*.py")]
            for i, line in enumerate(p.read_text("utf-8").splitlines(), 1)
            if pattern.search(line)
        ]
        assert hits == []

    def test_a_workers_fix_is_not_declared_as_a_float_anywhere(self) -> None:
        offenders = []
        for path in (APP / "models.py", APP / "inventory" / "putaway_schemas.py"):
            for i, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                if re.search(r"(lat|lng|accuracy)\w*\s*:\s*[^=]*\bfloat\b", line):
                    offenders.append(f"{path.name}:{i}")
        assert offenders == []
        text = (APP / "models.py").read_text("utf-8")
        assert re.search(r"lat_micro: Mapped\[int \| None\] = mapped_column\(BigInteger\)", text)
        assert "Float" not in re.findall(r"class PutawayCapture.*?(?=\nclass )", text, re.S)[0]
