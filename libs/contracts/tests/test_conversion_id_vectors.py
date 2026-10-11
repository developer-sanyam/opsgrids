"""The conversion-id golden vectors keep their shape.

`vectors/conversion_ids.v1.json` holds the ids lead's `conversion_ids` derives; lead, deal and
customer each assert their own copy of the function against it (three copies held equal by data,
not by an import, because `import app` is ambiguous across services). This file only checks the
data file itself, so a hand edit that drops or duplicates a vector is caught statically.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

VECTORS = Path(__file__).resolve().parents[1] / "vectors" / "conversion_ids.v1.json"


def test_the_vector_file_is_well_formed() -> None:
    data = json.loads(VECTORS.read_text())
    assert data["version"] == 1
    uuid.UUID(data["namespace"])
    vectors = data["vectors"]
    assert len(vectors) == 24
    assert {v["generation"] for v in vectors} == {0, 1, 2, 7}
    assert len({v["lead_id"] for v in vectors}) == 6
    triples = set()
    for v in vectors:
        for key in ("lead_id", "company_id", "contact_id", "deal_id"):
            uuid.UUID(v[key])
        triples.add((v["company_id"], v["contact_id"], v["deal_id"]))
    assert len(triples) == 24
