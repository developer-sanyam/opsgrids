"""This service's copy of lead's `conversion_ids` derives exactly the committed golden vectors.

The aborted-conversion handler acts only on the ids it re-derives from `(lead_id, generation)`,
so a drift between the copies would make it act on the wrong record. The three copies are held
equal by data (`libs/contracts/vectors/conversion_ids.v1.json`), not by an import.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from app import domain

VECTORS = (
    Path(__file__).resolve().parents[3]
    / "libs"
    / "contracts"
    / "vectors"
    / "conversion_ids.v1.json"
)


def test_conversion_ids_match_the_committed_vectors() -> None:
    data = json.loads(VECTORS.read_text())
    assert len(data["vectors"]) == 24
    for v in data["vectors"]:
        got = domain.conversion_ids(uuid.UUID(v["lead_id"]), v["generation"])
        assert tuple(str(i) for i in got) == (v["company_id"], v["contact_id"], v["deal_id"])


def test_generation_zero_has_no_suffix() -> None:
    lead_id = uuid.uuid4()
    company, _, _ = domain.conversion_ids(lead_id, 0)
    assert company == uuid.uuid5(domain.CONVERSION_NAMESPACE, f"{lead_id}:company")
    assert domain.conversion_ids(lead_id, 1)[0] != company


def test_the_namespace_constant_equals_the_vector_files_namespace() -> None:
    assert str(domain.CONVERSION_NAMESPACE) == json.loads(VECTORS.read_text())["namespace"]
