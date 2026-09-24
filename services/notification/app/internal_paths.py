"""Building a path for a call to a sibling service's internal API.

An id that ends up in a URL path is a **routing decision**, not just data. `httpx` resolves `..`
segments in an absolute URL before it sends the request, and a `?` or `#` ends the path early, so
an id that is a string somebody else chose — a field of an event payload a tenant's own users
typed, say — can walk a request off the route the code meant to call and onto another one on the
same host, carrying the same service token. That is how a "look up this contact" call becomes
"look up that *other organization's* contact".

So an id is never interpolated as the string it arrived as. It is parsed as a `uuid.UUID` and
rendered from the parsed value: the canonical text of a UUID is hex digits and hyphens and nothing
else, and `quote(..., safe="")` is kept on top of that so the guarantee does not depend on a
future reader knowing what a UUID's text can contain.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote


def uuid_segment(value: object) -> str | None:
    """One URL path segment for `value`, or `None` when it is not a UUID.

    Rendered from the parsed `uuid.UUID`, never from `value` itself: `uuid.UUID` accepts braces, a
    `urn:uuid:` prefix and other spellings, and none of those should reach a URL either.
    """
    try:
        parsed = uuid.UUID(str(value))
    except ValueError:
        return None
    return quote(str(parsed), safe="")
