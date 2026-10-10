#!/usr/bin/env bash
# Classifies a pull request's changed file paths into what .github/workflows/ci.yml's
# `changes` job needs: which halves of the repo the change could affect (`backend`,
# `frontend`, same meaning as before this script existed), and — within the backend half —
# which services specifically, when every changed path is narrow enough to trust
# (`services_all`, `services_list`, consumed by tools/filter-affected.sh via the
# AFFECTED_ALL / AFFECTED_SERVICES env vars it reads).
#
# Pulled out of the workflow file itself so it can be pinned by a real test
# (tests/infra/test_affected_areas.py) rather than trusted on read, the same reason
# infrastructure/edge/ci-plan.sh's path logic is a script and not inline YAML.
#
# This script does not know when to say "run everything regardless of what changed" —
# that is the `changes` job's call for an empty PR diff or a non-pull_request event, made
# by not invoking this script at all for those cases. What it does know is the one thing
# that would make trusting *its own* judgement unsafe: being handed nothing. Given no
# input at all it answers the same way — run everything — rather than claim a pull request
# touched nothing.
#
# `code` answers a narrower question than `backend`: could this change alter what a service's
# tests or migrations see? It is false only when *every* changed path is under docs/ — prose.
# The static job still runs for such a change (`backend` stays true: `ruff format` checks the
# Python inside Markdown fences, and tests/infra reads some docs), and so does the supply-chain
# job (the secret scan covers every tracked file), but the database-backed jobs — migrations,
# service tests, contracts — have nothing to look at. It is deliberately NOT widened to *.md
# anywhere or to infrastructure/: service tests read infrastructure/edge/RUNBOOK.md,
# routes.prod.json and the preflight libraries by path, so a change there can fail them.
#
# A rename is two paths, and both must be on stdin: the file's new path in `filename` and its
# old one in `previous_filename` (the pull-request files API reports the old one nowhere else).
# Classified by the new path alone, moving infrastructure/edge/RUNBOOK.md to docs/runbook.md is
# "only docs" — while the service test that reads the RUNBOOK by path is exactly what that move
# breaks. With both paths in, a move out of code is code whichever way it points. The workflow's
# jq expression is pinned against this script in tests/infra/test_affected_areas.py.
#
# usage: printf '%s\n' "${files[@]}" | tools/affected-areas.sh
#   reads changed file paths, one per line, on stdin (as `gh api .../files --jq
#   '.[] | .filename, (.previous_filename // empty)'` prints them); writes backend=, frontend=,
#   code=, services_all=, services_list= to stdout, one per line, GITHUB_OUTPUT format.
set -euo pipefail

backend=false
frontend=false
code=false
# Stays false only while every non-frontend path seen so far was services/<name>/... for
# some specific <name>. Anything else flips it, which is the conservative default this
# whole mechanism leans on.
services_all=false
services=""
saw_a_line=false

while IFS= read -r f; do
  [ -n "$f" ] || continue
  saw_a_line=true
  case "$f" in
  # Three frontend files that tests/infra reads as source (test_crm_spa_navigation.py and
  # test_engage_recipe_triggers.py). Those tests run in the backend `static` job, which a
  # frontend-only change would skip, so these count as backend too. Keep this ahead of
  # `frontend/*`; test_affected_areas.py holds the list. No service is named, so the defensive
  # block below turns services_all on: the safe direction.
  frontend/product-crm/src/main.tsx | frontend/product-crm/src/ui/useNavGroups.ts | frontend/product-engage/src/routes/automations/recipes.ts)
    backend=true
    frontend=true
    ;;
  frontend/*) frontend=true ;;
  docs/*)
    # Prose. Backend lint and the tooling tests still look at it; nothing database-backed
    # does — see `code` above. Says nothing about which services are affected, so it does not
    # touch services_all either: alone it narrows nothing, beside a service change it adds
    # nothing to the list.
    backend=true
    ;;
  .github/workflows/*)
    backend=true
    frontend=true
    code=true
    services_all=true
    ;;
  services/*/*)
    # services/<name>/anything — the one shape narrow enough to trust.
    backend=true
    code=true
    svc=$(echo "$f" | cut -d/ -f2)
    services="$services $svc"
    ;;
  *)
    # Everything else that is not under frontend/ — libs/platform-core (every service
    # depends on it), libs/contracts, root config (Makefile, pyproject.toml, uv.lock,
    # .importlinter, …), tools/, infrastructure/ — "fail open": not specific to one service,
    # so all of them run. (docs/ is handled above: it is the one prose-only path.)
    backend=true
    code=true
    services_all=true
    ;;
  esac
done

if [ "$saw_a_line" = "false" ]; then
  echo "affected-areas: no input — running everything rather than nothing" >&2
  backend=true
  frontend=true
  code=true
  services_all=true
fi

# services/approval/tests/test_attendance_round_trip.py and test_cross_service_contract.py
# deliberately reach into attendance's and employee's schemas to prove the cross-service
# contract holds (see the `test-services` comment in the Makefile). A change to either
# must still run approval's suite — it is the one that actually exercises the boundary.
case " $services " in
*" attendance "* | *" employee "*) services="$services approval" ;;
esac

# Defensive: backend=true but nothing survived into $services and nothing set
# services_all — should not be reachable given the case statement above, but a filter that
# quietly ran zero services because of a logic error here is a worse failure mode than one
# that ran all of them, so don't trust an empty list silently.
trimmed=$(echo "$services" | tr -d '[:space:]')
if [ "$backend" = "true" ] && [ "$services_all" = "false" ] && [ -z "$trimmed" ]; then
  services_all=true
fi

# Unquoted on purpose: $services is a sequence of names built up with plain concatenation
# above, and splitting it back into words — one per line, for sort -u — is exactly what
# this needs.
# shellcheck disable=SC2086
services_list=$(printf '%s\n' $services | sed '/^$/d' | sort -u | tr '\n' ' ')
services_list="${services_list% }"

echo "backend=$backend"
echo "frontend=$frontend"
echo "code=$code"
echo "services_all=$services_all"
echo "services_list=$services_list"
