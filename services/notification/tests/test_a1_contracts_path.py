"""The trigger catalogue must work in the runtime image, which holds `/app`, not the repository.

A first version located the contracts directory with `Path(__file__).parents[3]`. In a checkout
that is the repository root; in the container the file is `/app/app/a1_selectors.py`, which has
three parents, so importing the module raised `IndexError` and the service never started.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import a1_selectors


def test_the_lookup_does_not_raise_when_the_install_path_is_shallow() -> None:
    # The container layout: three parents and no `libs/contracts` anywhere above.
    assert a1_selectors.find_contracts_dir(Path("/app/app/a1_selectors.py")) is None


def test_the_lookup_finds_the_contracts_in_a_checkout() -> None:
    found = a1_selectors.find_contracts_dir(Path(a1_selectors.__file__))
    assert found is not None and found.name == "events" and found.is_dir()


def test_the_catalogue_lists_events_without_variables_when_there_are_no_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(a1_selectors, "_CONTRACTS", None)
    a1_selectors.trigger_catalogue.cache_clear()
    try:
        items = a1_selectors.trigger_catalogue()
        assert items and all(item["variables"] == [] for item in items)
        assert all(item["event_type"] for item in items)
    finally:
        a1_selectors.trigger_catalogue.cache_clear()
