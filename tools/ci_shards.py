#!/usr/bin/env python3
"""Plan which service tests run in which CI shard.

CI splits the service suites across runners rather than across processes (see the comment on
`test-shard` in the Makefile). The unit of work used to be a whole service, assigned to a shard
by `tools/shard.sh` from one number per service. That stopped working when one service,
`operations`, grew to 1,100+ seconds on its own: no assignment of whole services can finish
sooner than its slowest service, and a shard holding it took 32 minutes while its neighbours
took 7 and 12.

So the unit of work is now an *item*: a whole service, or — for a service too heavy for one
runner's share — one *part* of it. A part is a subset of the service's test **files**, chosen
by recorded duration (`tools/test-durations.json`). Files, not individual tests, because a
module's tests stay together and in order, which is how they were written and how they have
always run.

Three properties are what make this safe, and `verify` checks each of them:

  * **Nothing is dropped.** The files of a service's parts are exactly the test files on disk,
    each in exactly one part. The failure this prevents is the same silent one `shard-verify.sh`
    prevents for services: a miscount does not break a build, it stops a suite running.
  * **Parts of one service never share a shard.** Every shard is its own runner with its own
    Postgres, so two parts of `operations` can never truncate each other's tables. That is not
    luck: the service fixtures TRUNCATE their schema before every test, and two parts in one
    database would do it to each other mid-test. The assignment forbids it by construction.
  * **Deterministic.** Same tree, same durations file, same answer, in every job of a run —
    which is what lets each shard compute its own share without anyone passing a plan around.

usage:
    ci_shards.py items  <shard> <shards>   the items one shard runs, one per line
    ci_shards.py files  <item>             the test files of one item, one per line
    ci_shards.py needed <shards>           JSON list of the shards with work (honours
                                           AFFECTED_ALL / AFFECTED_SERVICES, like
                                           tools/filter-affected.sh)
    ci_shards.py digest <shards>           a short fingerprint of the whole plan. The `changes`
                                           job records it and every shard checks it, so a shard
                                           that sees a different tree (a sparse checkout that
                                           missed a directory, say) fails instead of quietly
                                           running a different plan
    ci_shards.py verify <shards>           assert the plan covers every test file exactly once
    ci_shards.py summary <shards>          the plan, with estimated seconds per shard
    ci_shards.py refresh <junit-dir>...    rewrite tools/test-durations.json from the junit
                                           XML files of a CI run (see docs/testing.md)

An item is spelled `<service>` (everything under `tests/`) or `<service>:<part>/<parts>`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"
DURATIONS = ROOT / "tools" / "test-durations.json"

# A test file nobody has measured yet. Deliberately middling: a new file is neither free nor
# the heaviest thing in the suite, and `verify` still guarantees it runs somewhere.
DEFAULT_FILE_SECONDS = 2.0

# What one pytest process costs before its first test: the interpreter, importing the app and
# collecting. Charged per item, so a service split into parts pays it per part.
ITEM_SECONDS = 4.0

# Work the first shard does that is not a service item: `make test-libs` and `make smoke` run
# there (see .github/workflows/ci.yml). Counted so the shard is not also handed a full share.
SHARD_BASE_SECONDS = {1: 45.0}

# An item no heavier than this fraction of a shard's fair share is never split; a heavier
# service is cut into parts of about this size. Half a share keeps the packing granular enough
# to land within a few percent of even, without paying the per-process cost on every service.
SPLIT_FRACTION = 0.5

_TEST_FILE = re.compile(r"^(test_.*|.*_test)\.py$")
_ITEM = re.compile(r"^(?P<service>[a-z0-9][a-z0-9-]*)(?::(?P<part>\d+)/(?P<parts>\d+))?$")


@dataclass(frozen=True)
class Item:
    """A whole service (`parts == 1`) or one part of it."""

    service: str
    part: int
    parts: int
    files: tuple[str, ...]  # relative to services/<service>/, e.g. "tests/test_stock.py"
    seconds: float

    @property
    def name(self) -> str:
        return self.service if self.parts == 1 else f"{self.service}:{self.part}/{self.parts}"


# ---------------------------------------------------------------- what is on disk


def services_with_tests() -> list[str]:
    """Every service whose `tests/` holds at least one test file, sorted.

    A service without tests has nothing to schedule. It used to be carried through the
    partition at a default weight and skipped later by `fan-out.sh`; leaving it out here means
    every job — including the `changes` job, which sees only a sparse checkout — computes the
    same plan from the same inputs.
    """
    found = []
    for directory in sorted(SERVICES.iterdir()):
        if directory.is_dir() and service_test_files(directory.name):
            found.append(directory.name)
    return found


def service_test_files(service: str) -> list[str]:
    """The files pytest would collect under `services/<service>/tests`, relative to the
    service directory, sorted. pytest's default patterns: `test_*.py` and `*_test.py`."""
    base = SERVICES / service
    tests = base / "tests"
    if not tests.is_dir():
        return []
    found = []
    for path in tests.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if _TEST_FILE.match(path.name):
            found.append(path.relative_to(base).as_posix())
    return sorted(found)


# ---------------------------------------------------------------- durations


def load_durations() -> dict[str, float]:
    """`{"<service>/tests/<file>": seconds}` from the committed file; empty if absent."""
    if not DURATIONS.exists():
        return {}
    data = json.loads(DURATIONS.read_text())
    files = data.get("files", {})
    return {str(k): float(v) for k, v in files.items()}


def file_seconds(durations: dict[str, float], service: str, rel: str) -> float:
    return durations.get(f"{service}/{rel}", DEFAULT_FILE_SECONDS)


# ---------------------------------------------------------------- the plan


def split_service(service: str, parts: int, durations: dict[str, float]) -> list[Item]:
    """Cut one service's test files into `parts` items, balanced by recorded duration.

    Longest-processing-time-first over files, ties broken by path, so the result is the same
    on every machine. Parts are numbered heaviest first, which also puts the part most likely
    to dominate its shard at the front of the queue.
    """
    files = service_test_files(service)
    parts = max(1, min(parts, len(files)))
    if parts == 1:
        total = sum(file_seconds(durations, service, f) for f in files)
        return [Item(service, 1, 1, tuple(files), total + ITEM_SECONDS)]

    ordered = sorted(files, key=lambda f: (-file_seconds(durations, service, f), f))
    buckets: list[list[str]] = [[] for _ in range(parts)]
    loads = [0.0] * parts
    for f in ordered:
        lightest = min(range(parts), key=lambda i: (loads[i], i))
        buckets[lightest].append(f)
        loads[lightest] += file_seconds(durations, service, f)

    ranked = sorted(range(parts), key=lambda i: (-loads[i], min(buckets[i])))
    return [
        Item(service, n + 1, parts, tuple(sorted(buckets[i])), loads[i] + ITEM_SECONDS)
        for n, i in enumerate(ranked)
    ]


def parts_for(service: str, shards: int, durations: dict[str, float], share: float) -> int:
    """How many parts one service is cut into: 1 unless it outweighs half a shard's share."""
    total = sum(file_seconds(durations, service, f) for f in service_test_files(service))
    limit = max(share * SPLIT_FRACTION, ITEM_SECONDS)
    return max(1, min(shards, math.ceil(total / limit)))


def build_items(shards: int, durations: dict[str, float] | None = None) -> list[Item]:
    if durations is None:
        durations = load_durations()
    names = services_with_tests()
    totals = {
        s: sum(file_seconds(durations, s, f) for f in service_test_files(s)) + ITEM_SECONDS
        for s in names
    }
    share = sum(totals.values()) / shards
    items: list[Item] = []
    for s in names:
        items.extend(split_service(s, parts_for(s, shards, durations, share), durations))
    return items


def assign(items: list[Item], shards: int) -> list[list[Item]]:
    """Longest-processing-time-first onto the lightest shard, never putting two parts of one
    service on the same shard. Deterministic: ties go to the lowest shard number."""
    plan: list[list[Item]] = [[] for _ in range(shards)]
    loads = [SHARD_BASE_SECONDS.get(i + 1, 0.0) for i in range(shards)]
    for item in sorted(items, key=lambda it: (-it.seconds, it.name)):
        candidates = [
            i for i in range(shards) if not any(x.service == item.service for x in plan[i])
        ]
        if not candidates:
            raise SystemExit(
                f"ci_shards: {item.service} has more parts than there are shards ({shards})"
            )
        target = min(candidates, key=lambda i: (loads[i], i))
        plan[target].append(item)
        loads[target] += item.seconds
    return plan


def plan(shards: int) -> list[list[Item]]:
    return assign(build_items(shards), shards)


def item_files(name: str) -> list[str]:
    """The test files of one item — what `pytest` is handed."""
    match = _ITEM.match(name)
    if not match:
        raise SystemExit(f"ci_shards: not an item name: {name!r}")
    service = match["service"]
    if not service_test_files(service):
        raise SystemExit(f"ci_shards: services/{service} has no test files")
    if match["part"] is None:
        return service_test_files(service)
    part, parts = int(match["part"]), int(match["parts"])
    cut = split_service(service, parts, load_durations())
    if len(cut) != parts or not 1 <= part <= parts:
        raise SystemExit(f"ci_shards: {name} is not a part of {service} (has {len(cut)} parts)")
    return list(cut[part - 1].files)


# ---------------------------------------------------------------- affected filter


def affected(services: list[str]) -> list[str]:
    """The same rule as tools/filter-affected.sh: AFFECTED_ALL=false with a populated
    AFFECTED_SERVICES narrows; anything else is everything."""
    if os.environ.get("AFFECTED_ALL", "true") != "false":
        return services
    wanted = set(os.environ.get("AFFECTED_SERVICES", "").split())
    if not wanted:
        return services
    return [s for s in services if s in wanted]


def shards_needed(shards: int) -> list[int]:
    """The shard numbers that have at least one affected item. Shard 1 is always included:
    it also runs the library tests and the boot smoke, which a service-local change must not
    skip (they are not scoped to a service)."""
    needed = {1}
    for number, shard_items in enumerate(plan(shards), start=1):
        if affected([it.service for it in shard_items]):
            needed.add(number)
    return sorted(needed)


def digest(shards: int) -> str:
    """A fingerprint of the plan: which files every item of every shard runs."""
    document = [[[it.name, list(it.files)] for it in shard] for shard in plan(shards)]
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


# ---------------------------------------------------------------- verify


def verify(shards: int) -> list[str]:
    """Problems with the plan, empty when it is sound."""
    problems: list[str] = []
    shard_plan = plan(shards)

    for number, shard_items in enumerate(shard_plan, start=1):
        if not shard_items:
            problems.append(f"shard {number}/{shards} has no work — always a bug in the plan")
        seen: set[str] = set()
        for it in shard_items:
            if it.service in seen:
                problems.append(f"shard {number}: two items of {it.service} share a database")
            seen.add(it.service)

    by_service: dict[str, list[str]] = {}
    for shard_items in shard_plan:
        for it in shard_items:
            by_service.setdefault(it.service, []).extend(it.files)
    expected = services_with_tests()
    for service in sorted(set(expected) | set(by_service)):
        planned = sorted(by_service.get(service, []))
        on_disk = service_test_files(service)
        if planned != on_disk:
            dropped = sorted(set(on_disk) - set(planned))
            invented = sorted(set(planned) - set(on_disk))
            dupes = sorted({f for f in planned if planned.count(f) > 1})
            problems.append(
                f"{service}: plan does not cover the test files exactly once "
                f"(dropped {dropped}, invented {invented}, duplicated {dupes})"
            )
        for f in on_disk:
            if re.search(r"\s", f):
                problems.append(f"{service}/{f}: a space in a test file name — rename it")
    return problems


def stale_durations() -> list[str]:
    """Entries in the durations file for a file that is no longer on disk (a warning)."""
    known = {f"{s}/{f}" for s in services_with_tests() for f in service_test_files(s)}
    return sorted(set(load_durations()) - known)


# ---------------------------------------------------------------- refresh from junit


def read_junit(directories: list[Path]) -> dict[str, float]:
    """Seconds per test file, summed from the junit XML files under `directories`.

    The file comes from each testcase's dotted `classname`. pytest roots that at the
    repository (`services.operations.tests.test_stock.TestAdjustStock`), so the longest
    dotted prefix that is a `.py` file under the repository is the test module. The time is
    pytest's own: setup, call and teardown summed, so the fixture's TRUNCATE is counted.
    """
    seconds: dict[str, float] = {}
    services_dir = SERVICES.resolve()
    for directory in directories:
        for xml_path in sorted(directory.rglob("*.xml")):
            # The files are our own CI's pytest output, downloaded by the person refreshing the
            # durations — not untrusted input, so the XML-attack concern behind S314 is absent.
            for case in ET.parse(xml_path).getroot().iter("testcase"):  # noqa: S314
                parts = case.get("classname", "").split(".")
                for cut in range(len(parts), 0, -1):
                    candidate = ROOT.joinpath(*parts[:cut]).with_suffix(".py")
                    if candidate.is_file() and services_dir in candidate.resolve().parents:
                        key = candidate.resolve().relative_to(services_dir).as_posix()
                        seconds[key] = seconds.get(key, 0.0) + float(case.get("time", 0))
                        break
    return seconds


def write_durations(seconds: dict[str, float], source: str) -> None:
    known = {f"{s}/{f}" for s in services_with_tests() for f in service_test_files(s)}
    kept = {k: round(v, 1) for k, v in sorted(seconds.items()) if k in known}
    document = {
        "_comment": (
            "Measured seconds per service test file, the weights tools/ci_shards.py balances "
            "the CI shards by. Absolute values do not need to be exact; the ratios between "
            "files do. Regenerate from a CI run's junit artifacts: docs/testing.md."
        ),
        "source": source,
        "files": kept,
    }
    DURATIONS.write_text(json.dumps(document, indent=1, sort_keys=False) + "\n")


# ---------------------------------------------------------------- CLI


def _summary(shards: int) -> str:
    lines = []
    shard_plan = plan(shards)
    loads = []
    for number, shard_items in enumerate(shard_plan, start=1):
        load = SHARD_BASE_SECONDS.get(number, 0.0) + sum(it.seconds for it in shard_items)
        loads.append(load)
        ordered = sorted(shard_items, key=lambda it: (-it.seconds, it.name))
        names = ", ".join(f"{it.name} {it.seconds:.0f}s" for it in ordered)
        lines.append(f"shard {number}/{shards}  {load:6.0f}s  {names}")
    lines.append(
        f"estimated: heaviest {max(loads):.0f}s, lightest {min(loads):.0f}s, "
        f"total {sum(loads):.0f}s over {sum(len(s) for s in shard_plan)} items"
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    command, args = argv[1], argv[2:]

    if command == "items":
        number, shards = int(args[0]), int(args[1])
        if not 1 <= number <= shards:
            print(f"ci_shards: shard {number} outside 1..{shards}", file=sys.stderr)
            return 2
        shard_items = plan(shards)[number - 1]
        for it in sorted(shard_items, key=lambda it: (-it.seconds, it.name)):
            print(it.name)
        return 0

    if command == "files":
        for f in item_files(args[0]):
            print(f)
        return 0

    if command == "needed":
        shards = int(args[0])
        # The dynamic matrix is only as good as the plan behind it: refuse to hand CI a list
        # computed from a tree that does not cover every test file (an empty or partial
        # checkout), because the shards it leaves out would simply never run.
        problems = verify(shards)
        if problems:
            for p in problems:
                print(f"ci_shards: {p}", file=sys.stderr)
            return 1
        print(json.dumps(shards_needed(shards)))
        return 0

    if command == "digest":
        print(digest(int(args[0])))
        return 0

    if command == "verify":
        shards = int(args[0])
        problems = verify(shards)
        for stale in stale_durations():
            print(f"ci_shards: note: {stale} is in test-durations.json but not on disk")
        if problems:
            for p in problems:
                print(f"ci_shards: {p}", file=sys.stderr)
            return 1
        names = services_with_tests()
        shard_plan = plan(shards)
        total_files = sum(len(service_test_files(s)) for s in names)
        print(
            f"ci-shards: {len(names)} services, {total_files} test files, "
            f"{sum(len(s) for s in shard_plan)} items across {shards} shards, "
            "every file exactly once"
        )
        return 0

    if command == "summary":
        print(_summary(int(args[0])))
        return 0

    if command == "refresh":
        directories = [Path(a) for a in args]
        seconds = read_junit(directories)
        if not seconds:
            print("ci_shards: no junit testcases found — nothing written", file=sys.stderr)
            return 1
        merged = {**load_durations(), **seconds}
        write_durations(merged, source="junit from " + ", ".join(args))
        print(f"ci_shards: wrote {len(seconds)} measured files into {DURATIONS.name}")
        return 0

    print(f"ci_shards: unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
