"""WhatsApp templates: Meta's shapes, ours, local validation and preview. Pure — no I/O.

Meta reviews a template for hours and rejects it with a reason nobody reads twice, so everything
that can be caught before it is submitted is caught here: the name, the `{{n}}` numbering, the
examples Meta demands, the length limits. The same module reads Meta's components JSON back into
the placeholders an operator fills, so a synced template and one created here reach the preview
and the send mapping through one code path.

Meta's components JSON is the source of truth for a template's wording (`ProviderTemplate.
components`); everything else on the row (`variable_specs`, the send-path `variables` mapping, the
preview text) is derived from it by `extract`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

#: Meta's status vocabulary, in ours. `None` means "no change to the status we hold": `FLAGGED`
#: is Meta warning that a template may be paused soon, not a new state of it, and inventing a
#: state for it would put a value in a column whose every reader (`template_is_sendable`, the
#: editor) only knows the six below.
STATUS_FROM_META: Final[dict[str, str | None]] = {
    "APPROVED": "approved",
    "REINSTATED": "approved",
    "PENDING": "pending",
    "IN_APPEAL": "pending",
    "REJECTED": "rejected",
    "PAUSED": "paused",
    "DISABLED": "disabled",
    "PENDING_DELETION": "disabled",
    # Archived (inactive; Meta deletes it after 28 days unless unarchived) and deleted templates
    # cannot be sent, so locally they are `disabled` — the one state `template_is_sendable` and
    # every editor already refuse. The webhook's own reason is kept in `status_reason`.
    "ARCHIVED": "disabled",
    "DELETED": "disabled",
    "FLAGGED": None,
    # `LOCKED` (template can no longer be edited) and `LIMIT_EXCEEDED` (the *WABA* is at its
    # template limit, not a state of one template) do not, per Meta's event descriptions, stop a
    # template being sent, so they change no status; they are named here so the webhook recognises
    # them and logs the event rather than treating them as unknown.
    "LOCKED": None,
    "LIMIT_EXCEEDED": None,
}

#: Events that put a template into a state Meta may later reverse with `UNARCHIVED`, which
#: "restores the template to its previous status" — see `w2_services.apply_status_change`.
ARCHIVE_EVENT: Final = "ARCHIVED"
UNARCHIVE_EVENT: Final = "UNARCHIVED"
#: Recorded as a history entry's `source` so `UNARCHIVED` can find what the template was before.
ARCHIVE_SOURCE: Final = "meta_webhook_archived"

CATEGORY_FROM_META: Final[dict[str, str]] = {
    "MARKETING": "marketing",
    "UTILITY": "utility",
    "AUTHENTICATION": "authentication",
}

QUALITY_FROM_META: Final[dict[str, str]] = {
    "GREEN": "high",
    "YELLOW": "medium",
    "RED": "low",
    "UNKNOWN": "unknown",
}

#: How many status changes a template remembers. A template that flips between approved and paused
#: every day for a year should not grow its own row without bound.
HISTORY_LIMIT: Final = 50

#: A `code` is at most 80 characters (`ProviderTemplate.code`); a name we create is its own code.
NAME_MAX: Final = 80
BODY_MAX: Final = 1024
HEADER_MAX: Final = 60
FOOTER_MAX: Final = 60
BUTTON_TEXT_MAX: Final = 25
URL_MAX: Final = 2000
EXAMPLE_MAX: Final = 1024
BUTTONS_MAX: Final = 10
URL_BUTTONS_MAX: Final = 2
PHONE_BUTTONS_MAX: Final = 1

_NAME = re.compile(r"^[a-z0-9_]+$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?$")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


# --------------------------------------------------------------------- vocabulary


def status_from_meta(value: object) -> str | None:
    """Our status for Meta's, or None for one that changes nothing (or one we do not know —
    Meta adds them, and a build that raised on a new one would fail on Meta's schedule)."""
    return STATUS_FROM_META.get(str(value or "").upper())


def category_from_meta(value: object) -> str | None:
    return CATEGORY_FROM_META.get(str(value or "").upper())


def category_after_update(value: dict[str, Any]) -> str | None:
    """The category a `template_category_update` says the template now bills as, or None for one
    that changes nothing yet.

    Meta sends two shapes (template_category_update reference):

    * **completed** — `previous_category` and `new_category`: the change has happened, so
      `new_category` is the category.
    * **impending** — `correct_category` and `category_update_timestamp`, with `new_category`
      naming the template's *current* category: in 24 hours Meta will recategorise it to
      `correct_category`. Only a move **to marketing** is applied early: marketing is the category
      that carries the opt-in obligation, so treating a template as marketing a day before Meta
      bills it as one is the safe direction; a move away from marketing waits for the completed
      event, because acting on it early would drop the obligation while it still applies.
    """
    correct = category_from_meta(value.get("correct_category"))
    if correct is not None:
        return correct if correct == "marketing" else None
    return category_from_meta(value.get("new_category"))


def quality_from_meta(value: object) -> str:
    """Meta's `quality_score` is either the bare word (a webhook) or `{score, date}` (a list)."""
    if isinstance(value, dict):
        value = value.get("score")
    return QUALITY_FROM_META.get(str(value or "").upper(), "unknown")


def history_entry(status: str, source: str, *, at: datetime | None = None) -> dict[str, str]:
    return {"status": status, "at": (at or datetime.now(UTC)).isoformat(), "source": source}


def appended_history(
    history: list[dict[str, Any]], status: str, source: str, *, at: datetime | None = None
) -> list[dict[str, Any]]:
    """`history` with one more entry, capped. A new list, so the JSONB column sees a change."""
    return [*history, history_entry(status, source, at=at)][-HISTORY_LIMIT:]


def unique_code(name: str, language: str, taken: set[str]) -> str:
    """The `code` a template is stored under: its own name if free, else name + language.

    Meta identifies a template by (name, language); ours by `code` alone, unique per organization
    and channel. So the second language of a name cannot reuse it.
    """
    if name[:NAME_MAX] not in taken:
        return name[:NAME_MAX]
    suffix = "_" + language.lower().replace("-", "_")
    candidate = name[: NAME_MAX - len(suffix)] + suffix
    counter = 2
    while candidate in taken:
        tail = f"{suffix}_{counter}"
        candidate = name[: NAME_MAX - len(tail)] + tail
        counter += 1
    return candidate


# --------------------------------------------------------------------- reading components


@dataclass(frozen=True, slots=True)
class Slot:
    """One placeholder, and where in the template it sits."""

    component: str  # body | header | button
    button_index: int
    token: str  # what stands between the braces in the text: "1" or "customer_name"
    spec: dict[str, Any]  # {index, name, kind, example}


@dataclass(frozen=True, slots=True)
class Extracted:
    body_preview: str
    slots: list[Slot] = field(default_factory=list)

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [dict(s.spec) for s in self.slots]

    @property
    def mapping(self) -> list[dict[str, Any]]:
        """The send path's `variables` (`domain.TemplateVariable`), one per slot.

        `key` is where a rule finds the value in its event payload. For a template that came from
        Meta we have no vocabulary of ours to offer, so it is the placeholder's own name — an
        operator who wants a different one re-maps it, and one who already did is not overwritten
        (`w2_services.apply_sync` only writes this when the slots themselves changed).
        """
        out: list[dict[str, Any]] = []
        counters: dict[tuple[str, int], int] = {}
        for slot in self.slots:
            group = (slot.component, slot.button_index)
            position = counters.get(group, 0)
            counters[group] = position + 1
            out.append(
                {
                    "key": str(slot.spec["name"]),
                    "component": slot.component,
                    "position": position,
                    "button_index": slot.button_index,
                    "kind": "text",
                    "parameter_name": slot.token if not slot.token.isdigit() else "",
                }
            )
        return out


def placeholders(text: str) -> list[str]:
    """The distinct placeholders in `text`, in the order Meta will fill them: numerically for
    `{{1}} {{2}}`, by first appearance for names."""
    seen: list[str] = []
    for match in _PLACEHOLDER.finditer(text):
        token = match.group(1)
        if token not in seen:
            seen.append(token)
    if seen and all(t.isdigit() for t in seen):
        return sorted(seen, key=int)
    return seen


def _examples(component: dict[str, Any], key: str, named_key: str, tokens: list[str]) -> list[str]:
    example = component.get("example")
    if not isinstance(example, dict):
        return [""] * len(tokens)
    named = example.get(named_key)
    if isinstance(named, list) and named:
        by_name = {
            str(n.get("param_name")): str(n.get("example") or "")
            for n in named
            if isinstance(n, dict)
        }
        return [by_name.get(t, "") for t in tokens]
    rows = example.get(key)
    values: list[Any] = []
    if isinstance(rows, list) and rows:
        first = rows[0]
        values = first if isinstance(first, list) else rows
    return [str(values[i]) if i < len(values) else "" for i in range(len(tokens))]


def extract(components: list[dict[str, Any]]) -> Extracted:
    """Read Meta's components JSON into a preview text and the placeholders it needs.

    Slots are numbered in the order an operator meets them: the body's first (so `{{1}}` is index
    1, as Meta numbers it), then the header's, then a dynamic URL button's.
    """
    body_text = ""
    slots: list[Slot] = []

    def add(
        component: str, button_index: int, token: str, name: str, kind: str, example: str
    ) -> None:
        slots.append(
            Slot(
                component=component,
                button_index=button_index,
                token=token,
                spec={"index": len(slots) + 1, "name": name, "kind": kind, "example": example},
            )
        )

    by_type = {
        str(c.get("type", "")).upper(): c
        for c in components
        if isinstance(c, dict) and c.get("type")
    }

    body = by_type.get("BODY")
    if body is not None:
        body_text = str(body.get("text") or "")
        tokens = placeholders(body_text)
        for token, example in zip(
            tokens, _examples(body, "body_text", "body_text_named_params", tokens), strict=True
        ):
            add("body", 0, token, token, "text", example)

    header = by_type.get("HEADER")
    if header is not None and str(header.get("format", "")).upper() == "TEXT":
        tokens = placeholders(str(header.get("text") or ""))
        for token, example in zip(
            tokens,
            _examples(header, "header_text", "header_text_named_params", tokens),
            strict=True,
        ):
            add("header", 0, token, "header", "text", example)

    buttons = by_type.get("BUTTONS")
    if buttons is not None:
        for i, button in enumerate(buttons.get("buttons") or []):
            if not isinstance(button, dict) or str(button.get("type", "")).upper() != "URL":
                continue
            tokens = placeholders(str(button.get("url") or ""))
            if tokens:
                sample = button.get("example")
                first = sample[0] if isinstance(sample, list) and sample else ""
                add("button", i, tokens[0], f"button_{i}", "url", str(first))

    return Extracted(body_preview=body_text, slots=slots)


def same_slots(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    """Do two send-path `variables` lists claim the same (component, button, position) slots?"""

    def keys(rows: list[dict[str, Any]]) -> set[tuple[Any, ...]]:
        return {(r.get("component"), r.get("button_index", 0), r.get("position", 0)) for r in rows}

    return len(a) == len(b) and keys(a) == keys(b)


# --------------------------------------------------------------------- preview


class PreviewError(ValueError):
    """Values are missing for placeholders the template needs."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("no value for: " + ", ".join(missing))
        self.missing = missing


def _fill(text: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def render_preview(components: list[Any], values: dict[str, str]) -> dict[str, Any]:
    """The template as a customer would read it, given `values` keyed by slot index or name.

    Strict, like `domain.render`: a placeholder with no value raises `PreviewError` rather than
    rendering a hole, because a preview that hides one is a preview that lies about the message.
    """
    extracted = extract(components)
    resolved: dict[tuple[str, int], dict[str, str]] = {}
    missing: list[str] = []
    for slot in extracted.slots:
        value = values.get(str(slot.spec["index"]))
        if value is None:
            value = values.get(str(slot.spec["name"]))
        if value is None:
            missing.append(str(slot.spec["index"]))
            continue
        resolved.setdefault((slot.component, slot.button_index), {})[slot.token] = value
    if missing:
        raise PreviewError(missing)

    header: str | None = None
    footer: str | None = None
    body = ""
    buttons: list[dict[str, Any]] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        ctype = str(component.get("type", "")).upper()
        if ctype == "HEADER" and str(component.get("format", "")).upper() == "TEXT":
            header = _fill(str(component.get("text") or ""), resolved.get(("header", 0), {}))
        elif ctype == "BODY":
            body = _fill(str(component.get("text") or ""), resolved.get(("body", 0), {}))
        elif ctype == "FOOTER":
            footer = str(component.get("text") or "")
        elif ctype == "BUTTONS":
            for i, button in enumerate(component.get("buttons") or []):
                if not isinstance(button, dict):
                    continue
                kind = {"QUICK_REPLY": "quick_reply", "URL": "url", "PHONE_NUMBER": "phone"}.get(
                    str(button.get("type", "")).upper()
                )
                if kind is None:
                    continue
                entry: dict[str, Any] = {"type": kind, "text": str(button.get("text") or "")}
                if kind == "url":
                    entry["url"] = _fill(
                        str(button.get("url") or ""), resolved.get(("button", i), {})
                    )
                if kind == "phone":
                    entry["phone"] = str(button.get("phone_number") or "")
                buttons.append(entry)

    text = "\n\n".join(part for part in (header, body, footer) if part)
    return {"text": text, "header": header, "body": body, "footer": footer, "buttons": buttons}


# --------------------------------------------------------------------- validation on create


def _problem(field_name: str, message: str) -> dict[str, str]:
    return {"field": field_name, "message": message}


def validate_create(
    *,
    name: str,
    category: str,
    language: str,
    components: dict[str, Any],
) -> list[dict[str, str]]:
    """Every reason Meta would refuse this template that we can see without asking it.

    Returns all of them, not the first: an operator fixing a form wants the whole list. Empty
    means "worth submitting", not "will be approved" — Meta's review still has the last word.
    """
    problems: list[dict[str, str]] = []

    if not name or not _NAME.match(name):
        problems.append(_problem("name", "use lowercase letters, digits and underscores only"))
    elif len(name) > NAME_MAX:
        problems.append(_problem("name", f"at most {NAME_MAX} characters"))
    if not _LANGUAGE.match(language):
        problems.append(_problem("language", "a Meta language code such as en, en_US or hi"))
    if category == "authentication":
        problems.append(
            _problem(
                "category",
                "authentication templates use Meta's one-time-password components, "
                "which cannot be authored here",
            )
        )

    body = components.get("body") or {}
    body_text = str(body.get("text") or "")
    examples = [str(e) for e in (body.get("examples") or [])]
    _validate_body(body_text, examples, problems)

    header = components.get("header")
    if header:
        _validate_header(header, problems)

    footer = components.get("footer")
    if footer:
        footer_text = str(footer.get("text") or "")
        if not footer_text.strip():
            problems.append(_problem("components.footer.text", "required when a footer is set"))
        elif len(footer_text) > FOOTER_MAX:
            problems.append(_problem("components.footer.text", f"at most {FOOTER_MAX} characters"))
        elif _PLACEHOLDER.search(footer_text):
            problems.append(_problem("components.footer.text", "a footer cannot have variables"))

    _validate_buttons(components.get("buttons") or [], problems)
    return problems


def _validate_body(text: str, examples: list[str], problems: list[dict[str, str]]) -> None:
    field_name = "components.body.text"
    if not text.strip():
        problems.append(_problem(field_name, "required"))
        return
    if len(text) > BODY_MAX:
        problems.append(_problem(field_name, f"at most {BODY_MAX} characters"))
    stripped = _PLACEHOLDER.sub("", text)
    if "{{" in stripped or "}}" in stripped:
        problems.append(_problem(field_name, "a variable must be written {{1}}, {{2}}, ..."))
        return
    tokens = placeholders(text)
    if any(not t.isdigit() for t in tokens):
        problems.append(_problem(field_name, "variables are numbered {{1}}, {{2}}, ..."))
        return
    numbers = sorted(int(t) for t in tokens)
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(
            _problem(
                field_name,
                f"variables must be numbered 1..{len(numbers)} without gaps; found {numbers}",
            )
        )
        return
    if tokens:
        if _PLACEHOLDER.match(text.strip()) or re.search(r"\{\{\s*\d+\s*\}\}$", text.strip()):
            problems.append(_problem(field_name, "a variable cannot start or end the body"))
        if len(examples) != len(tokens):
            problems.append(
                _problem(
                    "components.body.examples",
                    f"{len(tokens)} example(s) required, one per variable; got {len(examples)}",
                )
            )
    elif examples:
        problems.append(
            _problem("components.body.examples", "the body has no variables, so no examples")
        )
    for i, example in enumerate(examples):
        if not example.strip():
            problems.append(_problem(f"components.body.examples.{i}", "cannot be blank"))
        elif len(example) > EXAMPLE_MAX:
            problems.append(
                _problem(f"components.body.examples.{i}", f"at most {EXAMPLE_MAX} characters")
            )


def _validate_header(header: dict[str, Any], problems: list[dict[str, str]]) -> None:
    kind = header.get("type")
    example = header.get("example")
    if kind == "text":
        text = str(header.get("text") or "")
        if not text.strip():
            problems.append(_problem("components.header.text", "required for a text header"))
            return
        if len(text) > HEADER_MAX:
            problems.append(_problem("components.header.text", f"at most {HEADER_MAX} characters"))
        tokens = placeholders(text)
        if tokens and tokens != ["1"]:
            problems.append(
                _problem("components.header.text", "a header can hold only one variable, {{1}}")
            )
        elif tokens and not (example and str(example).strip()):
            problems.append(
                _problem("components.header.example", "required when the header has a variable")
            )
        elif not tokens and example:
            problems.append(
                _problem("components.header.example", "the header has no variable, so no example")
            )
    else:
        if header.get("text"):
            problems.append(_problem("components.header.text", "only a text header has text"))
        if not (example and str(example).strip()):
            problems.append(
                _problem(
                    "components.header.example",
                    "required for a media header: Meta's upload handle for a sample file",
                )
            )


def _validate_buttons(buttons: list[dict[str, Any]], problems: list[dict[str, str]]) -> None:
    if len(buttons) > BUTTONS_MAX:
        problems.append(_problem("components.buttons", f"at most {BUTTONS_MAX} buttons"))
    if sum(1 for b in buttons if b.get("type") == "url") > URL_BUTTONS_MAX:
        problems.append(_problem("components.buttons", f"at most {URL_BUTTONS_MAX} URL buttons"))
    if sum(1 for b in buttons if b.get("type") == "phone") > PHONE_BUTTONS_MAX:
        problems.append(_problem("components.buttons", f"at most {PHONE_BUTTONS_MAX} phone button"))
    for i, button in enumerate(buttons):
        prefix = f"components.buttons.{i}"
        text = str(button.get("text") or "")
        if not text.strip():
            problems.append(_problem(f"{prefix}.text", "required"))
        elif len(text) > BUTTON_TEXT_MAX:
            problems.append(_problem(f"{prefix}.text", f"at most {BUTTON_TEXT_MAX} characters"))
        kind = button.get("type")
        if kind == "url":
            _validate_url_button(button, prefix, problems)
        elif kind == "phone":
            if not _E164.match(str(button.get("phone") or "")):
                problems.append(
                    _problem(f"{prefix}.phone", "an E.164 number such as +919876543210")
                )
        elif kind == "quick_reply" and (button.get("url") or button.get("phone")):
            problems.append(_problem(prefix, "a quick reply has no url or phone"))


def _validate_url_button(
    button: dict[str, Any], prefix: str, problems: list[dict[str, str]]
) -> None:
    url = str(button.get("url") or "")
    if not re.match(r"^https?://[^\s]+$", url):
        problems.append(_problem(f"{prefix}.url", "an http(s) URL"))
        return
    if len(url) > URL_MAX:
        problems.append(_problem(f"{prefix}.url", f"at most {URL_MAX} characters"))
    tokens = placeholders(url)
    if tokens and (tokens != ["1"] or not re.search(r"\{\{\s*1\s*\}\}$", url)):
        problems.append(
            _problem(f"{prefix}.url", "a URL can hold one variable, {{1}}, at its very end")
        )
    elif tokens and not str(button.get("example") or "").strip():
        problems.append(
            _problem(f"{prefix}.example", "required when the URL has a variable: a full sample URL")
        )


def to_meta_components(components: dict[str, Any]) -> list[dict[str, Any]]:
    """Our create body's components as Meta's `POST /{waba}/message_templates` wants them."""
    out: list[dict[str, Any]] = []
    header = components.get("header")
    if header:
        kind = str(header["type"])
        if kind == "text":
            entry: dict[str, Any] = {"type": "HEADER", "format": "TEXT", "text": header["text"]}
            if header.get("example"):
                entry["example"] = {"header_text": [header["example"]]}
        else:
            entry = {
                "type": "HEADER",
                "format": kind.upper(),
                "example": {"header_handle": [header.get("example")]},
            }
        out.append(entry)

    body = components["body"]
    body_entry: dict[str, Any] = {"type": "BODY", "text": body["text"]}
    if body.get("examples"):
        body_entry["example"] = {"body_text": [list(body["examples"])]}
    out.append(body_entry)

    footer = components.get("footer")
    if footer:
        out.append({"type": "FOOTER", "text": footer["text"]})

    buttons = components.get("buttons") or []
    if buttons:
        meta_buttons: list[dict[str, Any]] = []
        for button in buttons:
            kind = button["type"]
            if kind == "quick_reply":
                meta_buttons.append({"type": "QUICK_REPLY", "text": button["text"]})
            elif kind == "url":
                item: dict[str, Any] = {"type": "URL", "text": button["text"], "url": button["url"]}
                if button.get("example"):
                    item["example"] = [button["example"]]
                meta_buttons.append(item)
            else:
                meta_buttons.append(
                    {
                        "type": "PHONE_NUMBER",
                        "text": button["text"],
                        "phone_number": button["phone"],
                    }
                )
        out.append({"type": "BUTTONS", "buttons": meta_buttons})
    return out
