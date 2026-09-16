# Approval engine — screen specifications for `frontend/product-hrms`

**Status: design, 2026-09-16. Revised twice on 2026-09-16, against both ADR-0006 amendments.**
Specifies the HRMS surfaces for [ADR-0006](../adr/0006-approval-engine.md). Design only — no
component code.

Built against: ADR-0006 (routing, delegation, eligibility, escalation, permissions, events) **as
amended 2026-09-16**, [ADR-0007](../adr/0007-permission-rollout-to-live-tenants.md) (how a new
permission reaches a live tenant — an operational precondition for every screen here, §2.8),
[ADR-0001](../adr/0001-frontend-topology.md) (one app per surface, shared `@opsgrids/ui`),
[ADR-0003 §3](../adr/0003-surfaces-and-authorization-topology.md) (a UI gate hides a control; the
server decides), [ADR-0000 *Also settled*](../adr/0000-phase-0-decisions.md) (timestamps render in
the relevant location's timezone, never the browser's), and `frontend/CLAUDE.md`.

Every component named in this document is either **(a)** exported today from
`frontend/opsgrids-ui/src/index.ts`, or **(b)** listed in §8 as a gap with the API it must expose.
Nothing else is assumed to exist. Where a screen needs a field no endpoint returns today, the field
is listed in §9 as a contract dependency rather than quietly designed around.

> **What the ADR amendments changed in this document.** ADR-0006 §14 names this file in two rows
> (§4.A and §9.2). It was amended **twice** before merge: the first pass granted the five contract
> dependencies this spec raised and settled three of its open questions; the second answered the six
> response-shape questions §9.2 then raised. Both are reflected below.
>
> **Second amendment — what moved:**
>
> | Change | Where |
> |---|---|
> | **Rejections are reported back too** — the return event means "I applied your decision", not "I corrected the day", so a landed rejection is `applied`. New **`Rejected — not applied`** state | §2.10, §4.B, §4.C, §4.D |
> | **Detail read widens to anyone who appears on a step, any outcome** — past participants keep access to what they decided. The past-decider 404 is removed | §3.2, §4.B, §5 |
> | `request_note`'s audience widens to match, and it travels on the **inbox list and detail**, not `/requests` | §2.9, §4.A |
> | `location_code` on every instant-rendering response (`requester_` / `delegator_` / subject), with a **visibly labelled** organization-time fallback | §2.1, §4.E, §4.F |
> | `employee_no` accompanies the person being *identified* — requester, chain rung, candidate — not approver/delegate/decider | §2.2, §4.A, §4.F |
> | `application_state` also on `GET /requests`, so "My requests" shows the qualified value; never on the inbox | §2.10, §4.D |
> | The whole return path ships in **slice 2**, confirmed rather than assumed | §6 |
>
> **First amendment — what moved:**
>
> | Change | Where |
> |---|---|
> | Names ship on every approval response (`*_name`); `display_name` + `employee_no` on the hierarchy copy | §2.2, §4.A–§4.F |
> | `inbox_reason` + `from_name` per inbox item — **six** values, not five | §2.3, §4.A |
> | The requester's free text is **`request_note`**, no longer part of `summary`, with stated visibility rules | §2.9, §4.A, §4.B, §4.C |
> | A decision can be **applied or fail afterwards** — a new "Approved, not applied" state | §2.10, §3.1, §3.2, §4.B, §4.C, §4.D |
> | `GET /delegate-candidates?q=` replaces the employee picker's source | §4.E, §5 |
> | `repointed_count` granted; per-row `open_request_count` refused | §4.E |
> | Reject comment optional is now a **server** rule (§7), not a UI choice | §4.B |
> | **Slices 2–3 open every step with no due date** — no SLA countdown, no overdue styling, no Due column, no overdue banner until slice 4 | §4.A, §4.B, §4.G, §6 |
> | The inbox's department sub-line is **not available** — `department_id` was dropped from the copy | §4.A |
> | Approval permissions reach live tenants only through the ADR-0007 reconcile | §2.8, §5, §6 |
> | `hrms.time.attendance.request` is now a declared permission, `max_scope = OWN` | §5, §9 |

---

## 1. Information architecture

### 1.1 The object model, in the user's words

| The user says | The system calls it | Where it lives |
|---|---|---|
| A request | `approval_requests` row + the product's own row | Approvals |
| A correction / "fixing my attendance" | `regularization_requests` (attendance) | Attendance record → Approvals |
| What's being asked | `summary` JSONB — structured facts only (date, from, to) | Inbox, request detail |
| The reason they gave | `request_note` — the requester's own free text, a separate column | Inbox, request detail |
| Who it's waiting on | the open `approval_steps` row's approver | Inbox, request detail |
| Whether the answer actually landed | `application_state` (`applied` / `failed`) | Request detail, attendance record |
| The history | `approval_steps`, append-only | Request detail |
| Standing in for me | `delegations` | Delegations |
| Who approves my requests | the resolved chain over `hierarchy_nodes` | Employee page |
| How long an approver gets | `approval_policies.sla_minutes` | Approval settings (slice 4) |

**Never shown to a user:** "step", "outcome", "source_ref", "request_type", "hierarchy node",
"escalation sweep", "policy row", any UUID. The word *step* appears nowhere in the interface; the
history reads as sentences. The word *request type* is shown once, on the settings screen, as the
plain name **"Attendance correction"**.

**Vocabulary, fixed for the whole product.** Use these exact words everywhere, including in future
request types:

- **Request** (not "approval", not "ticket"). A person **raises** a request.
- **Approve** / **Reject** — the two decisions. Past tense: **Approved** / **Rejected**.
- **Waiting on {Name}** — the pending state, always naming a person.
- **Unassigned** — nobody in the chain can be found. Never "orphaned", never "failed routing".
- **Delegation**: "{Delegate} decides for {Delegator}". The verb is **delegate**; the control is
  **Set up a delegation**; ending one is **Revoke**.
- **Escalated** — "moved up to {Name} because nobody answered in time".
- **Attendance correction** — the user-facing name of `hrms.time.regularization`. The employee's
  own action on their record is **Request a correction**.
- **Reason** — the user's word for `request_note`, the free text the requester typed. Never "note",
  never "comment" (a **comment** is what the *decider* writes). One word per object, always.
- **Couldn't be applied** — a decision that was made but did not land on the record
  (`application_state = 'failed'`). Never "error", never "sync failed", never "partially approved".
  The decision stands; only the record didn't move.
- **Due** / **Overdue** — the SLA. Never "SLA" in the interface except on the settings screen,
  where it is explained in the same breath. **Neither word appears anywhere in the interface before
  slice 4** — see §2.11.

### 1.2 Navigation

The HRMS sidebar (`frontend/product-hrms/src/ui/Sidebar.tsx`) has three groups plus two pinned
entries. **There is no existing `ComingSoon` slot for approvals** — `/tasks` is a different object
(assignable people-ops to-dos, already previewed by the dashboard's `TasksToday` widget) and taking
it would rename a planned concept. Approvals gets its own group, built from the existing
`NavGroup` / `NavEntry` shape, so no sidebar component change is needed.

New group, inserted **between "Main Menu" and "Records"**:

```
Approvals
  Waiting on me      /approvals                InboxIcon         count = open items
  My requests        /approvals/mine           ClipboardListIcon
  Delegations        /approvals/delegations    ReplyIcon
  Approval settings  /approvals/settings       SettingsIcon      (hidden without policy.write:ORG)
```

Group title **"Approvals"**. Item labels are the user's words for the question each answers:
"Waiting on me" answers *what do I have to do*, "My requests" answers *what did I ask for*.

`count` on "Waiting on me" is the quiet grey count (`NavEntry.count`), not the attention `badge`.
Rationale: the badge pill's tone is a sidebar-wide prop (`badgeTone`), so an overdue-red badge here
would recolour the Notifications badge too. Overdue is surfaced on the screen, not in the rail — and
not at all before slice 4 (§2.11), so the count stays a plain count in every slice. See gap **G12**.

No other sidebar entry changes. Attendance keeps its place; the regularisation entry point lives
inside the Attendance screen because it belongs to a record, not to a menu.

### 1.3 Routes

| Route | Screen | Slice |
|---|---|---|
| `/approvals` | Approvals inbox (§4.A) | 2 |
| `/approvals/requests/:requestId` | Request detail (§4.B) | 2 |
| `/approvals/mine` | My requests (§4.D) | 2 |
| `/approvals/delegations` | Delegations (§4.E) | 3 |
| `/approvals/settings` | Approval settings (§4.G) | 4 |
| `/attendance` | Attendance — gains the correction entry point and the pending marker (§4.C) | 2 |
| `/employees/:employeeId` | Employee — **new route**, hosts the approval chain (§4.F) | 1 |

`/employees/:employeeId` does not exist today (`Employees.tsx` is a card grid with no detail
route). Slice 1 adds it; §4.F specifies the minimum host page as well as the chain section.

All approval routes sit inside the existing `RequireAuth` + `AppShell` children array in
`main.tsx`. None is a `ComingSoonPage`.

### 1.4 Where every entry point lands

| Entry point | Lands on | Then |
|---|---|---|
| Sidebar "Waiting on me" | `/approvals`, "Waiting on me" filter, sorted oldest-first | — |
| Sidebar "My requests" | `/approvals/mine`, newest first | — |
| Inbox row (click / Enter / Space) | `/approvals/requests/:id` | Back returns to the inbox with the filter and page preserved |
| Approve or Reject completes | back to `/approvals`, decided row gone, confirmation announced | — |
| Decision on a request opened from "My requests" | stays on the detail (the requester is never a decider, so this path only happens for an ORG reader) | — |
| Attendance row "Request a correction" | the correction dialog, over `/attendance` | On success: dialog closes, the row shows "Correction pending" |
| Attendance row "Correction pending · Waiting on Priya Nair" | `/approvals/requests/:id` when the viewer may read it; otherwise not a link | — |
| "Set up a delegation" | `/approvals/delegations` with the form open | On success: list, new delegation first |
| Employee page → "Approval chain" | in-page section, no navigation | — |
| Dashboard (future) | not in scope for these slices | — |
| Deep link to a request the caller may not read | server 404 → "That request isn't available to you" (§3.9) | — |

---

## 2. Cross-screen foundations

These are binding on every screen below. They exist so two engineers building different screens
produce the same thing.

### 2.1 Time, always in the location's timezone

ADR-0000 settles it: `TIMESTAMPTZ` in UTC, rendered in **the relevant location's** timezone, never
the browser's. `frontend/product-hrms/src/lib/format.ts` currently hard-codes `timeZone: "UTC"`,
which is not the same thing and must not be reused for approvals.

**Which location is "relevant":**

**Resolved by the amendment (O4 — this was blocking slice 1).** Every approval response that renders
an instant now carries the location code that governs it, and the ADR names the field per surface:

| Surface | Timezone | Field on the response | Why |
|---|---|---|---|
| A request (inbox, my requests, detail) | the **requester's** location | `requester_location_code` | The request is about their working day |
| A delegation's start and end | the **delegator's** location | `delegator_location_code` — on delegation rows **and** on the create response | The person handing over is the one whose office hours matter |
| The approval chain | the **subject employee's** location | `location_code` on `GET /chain` | — |
| An attendance record and its correction | the **record's** location | attendance's own contract — **see C9** | The day boundary belongs to the site |
| SLA in settings | no instant is shown, only a duration | none (`/policies` carries no code) | Durations have no zone |
| The delegate picker | no instant is shown | none (`/delegate-candidates` carries no code) | — |

**Resolution order:** the response's location code → the matching `code` in
`GET /api/v1/locations` (`LocationOut.timezone`, IANA) → if the code is `null` or matches nothing,
`GET /api/v1/organization`.`timezone`, **visibly labelled** (below). The locations list is small,
org-wide readable, and fetched once per session and cached; approvals never fetch it per row.

**Approval returns the code, not a resolved IANA zone**, and the client must not expect otherwise:
the zone belongs to `account.locations`, the SPA already caches that list, and copying a third
service's fact onto the approval copy is how the dropped `department_id` problem started. The
conversion happens at the edge, every time.

**Display format**, fixed:

- Absolute: `Tue 15 Sep, 6:40 pm IST` — weekday, day, short month, 12-hour lowercase, zone
  abbreviation. Year is added only when the instant is not in the current calendar year.
- Attendance dates (no time): `12 Sep 2026`.
- Relative, used only alongside an absolute value: `2 days ago`, `4 hours ago`, `just now`.
  Relative is the primary read for "waiting since"; the absolute sits in the `title` attribute
  **and** in the detail screen's body text, never only in a tooltip.
- Overdue: `Overdue by 6 hours` — never a negative duration. **Slice 4 only**; no due or overdue
  value is rendered anywhere before it (§2.11).
- **When the zone has been resolved by fallback** (the code is `null`, or matches no location), the
  abbreviation is the organization's and the instant carries a **visible** label —
  `15 Sep, 6:40 pm IST · organization time` — not a tooltip. The ADR makes this mandatory: *a
  silently wrong zone is worse than an obviously approximate one, because a due time is what an
  approver acts on.* A `title` of `Organization time (Asia/Kolkata)` carries the full zone name in
  addition, never instead.
- **The browser's timezone is never used**, in any state, including the fallback.

Every timestamp in the interface carries its zone abbreviation. A bare "6:40 pm" is a bug.

See gaps **G9** (no zone-aware formatting helper) and **G10** (no zone-aware datetime input).

### 2.2 Referring to a person

**Resolved by the ADR amendment (C1, granted).** `hierarchy_nodes` now carries `display_name` and
`employee_no`, fed by the same full-state employee events, and every approval response returns a
name beside every id: `requester_name`, `approver_name`, `acting_for_name`, `decided_by_name`,
`delegator_name`, `delegate_name` (ADR-0006 §2, §11). **The client never resolves a name itself** —
it does not call the employees API to render an approval screen, and it must not start, because an
ordinary approver's `hrms.people.employee.read:OWN` resolves nobody but themselves.

The **PersonRef** display is one pattern, used identically everywhere:

- **Inline** (table cell, chip, sentence): `Avatar size="xs"` + the `*_name` value. Name only, no id.
- **Block** (inbox requester cell, detail header, chain rung, delegate picker option):
  `Avatar size="sm"` + name over `employee_no` in `text-xs text-muted tnum`.

**Where `employee_no` appears is a rule, not a preference (C6, resolved).** It accompanies the
person being **identified**, never every person named:

| Carries `employee_no` | Does not |
|---|---|
| the **requester** — on inbox items, `GET /requests` items and the detail | `approver_name`, `delegate_name`, `decided_by_name`, `acting_for_name`, `from_name` |
| each **rung** on `GET /chain` | — |
| each **candidate** on `/delegate-candidates` | — |

The reasoning, which the interface must not quietly invert: an employee number disambiguates *the
subject of a decision* — two colleagues with the same name, and an approver who approves the wrong
person's correction has made a real and unrecoverable error — whereas "who is holding it" is
context, not identification. So the **block** variant is used wherever the number travels, and the
**inline** variant everywhere else; a block with no number renders name only, never a blank second
line and never reserved space for one.
- **Name unavailable** — the ADR is explicit that an unresolvable name is `null` rather than absent,
  *and that the client renders its own fallback so the row stays operable*. The name renders as
  **"Employee record unavailable"** in `text-muted`, the avatar falls back to a neutral tint, and the
  `title` carries the employee number when one is known. An approver can still decide a request whose
  requester's name came back null. A null name is never a reason to hide a row or disable an action.

**A null name is expected, not exceptional.** It is what a requester who is not yet in the hierarchy
copy looks like (ADR-0006 §4: the backfill has not reached this organization, or an `onboarded` event
is lagging) — the same population whose step opens unassigned. Both facts appear on the same row, and
the copy must not read as two separate faults.

### 2.3 Why a request is in my inbox

**Resolved by the ADR amendment (C2, granted).** `GET /inbox` returns `inbox_reason` per item, plus
`from_name` where the reason names somebody. The client **maps the server's value to a chip and
derives nothing** — re-deriving the reason in the browser means re-implementing ADR-0006 §6 there,
which is the one place it must never live.

**Six values, not the five this spec originally proposed.** The ADR splits "still mine" into two
reasons, because *delegated away* and *escalated away* are different facts with different next
actions: one is a person you chose to hand to, the other is time running out. The chips follow:

| `inbox_reason` | Chip copy | What it means (ADR-0006 §6) |
|---|---|---|
| `assigned` | `Assigned to you` | the open step's approver is you, no delegation |
| `delegated_to_me` | `Delegated from {from_name}` | the open step's approver is you, standing in for {from_name} — route (a) on a delegated step |
| `escalated_to_me` | `Escalated to you from {from_name}` | the step moved up to you because {from_name} didn't answer in time |
| `delegated_away` | `Still yours — {from_name} is standing in` | you handed this to {from_name}; you remain eligible (route (b)) |
| `escalated_away` | `Still yours — now with {from_name}` | nobody answered in time and it moved up to {from_name}; you remain eligible (route (a) on the escalated step) |
| `unassigned` | `Unassigned — no approver found` | the open step has no approver; you see it because you hold `decide:ORG` |

- **`from_name` null** (the ADR permits a null name on any id): the chip drops the name rather than
  rendering "undefined" — `Delegated to you`, `Escalated to you`, `Still yours — someone else has it`.
  The chip never disappears; the reason matters more than the name.
- **An unrecognised `inbox_reason`** (a value added by a later request type): render no chip rather
  than a wrong one, and keep the row operable. The chip is an explanation, not a gate.
- `escalated_to_me` and `escalated_away` cannot occur before slice 4 (nothing escalates without a due
  date, §2.11). They are specified now so the mapping is complete, and they ship in slice 4.

Rendered with `Tag` (neutral) except **Unassigned**, which uses a warning-toned `Tag` variant, and
carries no colour-only meaning — the words say it.

### 2.4 Permission gating — what hides, what the server enforces

`@opsgrids/client` exports no `usePermission`, `RequirePermission` or `useEntitlement` today, and
`MeOut` carries `roles`, not grants (gap **G1**). The rule from ADR-0003 §3 is unchanged: **hiding
is a courtesy; the server decides.** This spec therefore gates in three ways, in preference order:

1. **Let the server's own filtering be the gate.** `GET /inbox` returns only what the caller may
   decide, including unassigned items only for `decide:ORG` holders. So the inbox needs no
   client-side permission check at all — if unassigned rows come back, the caller is an ORG decider,
   and the "Unassigned" filter chip is rendered only when the current result set has ever contained
   one. This is the preferred pattern and is used wherever possible.
2. **Hide a nav entry and a control with `usePermission`** where the server cannot answer the
   question by filtering: "Approval settings" (`core.approval.policy.write:ORG`) and the Delegations
   "for another employee" mode (`core.approval.delegation.write:ORG`). Both depend on **G1**.
3. **Never disable a control the caller cannot use.** A disabled button with no explanation is worse
   than an absent one. Where a control is hidden, the screen still says what is possible and who to
   ask (see each screen's *no permission* state).

**If G1 slips**, the fallback is: the nav entry stays hidden for everyone, the route remains
reachable by URL, and an unauthorised caller gets the server's 403 rendered as the permission-denied
state. Shipping the control to everyone and letting it fail is not an option.

**What the server still enforces regardless of any of this** (state this in the PR description, not
in the UI): the `decide` permission *and* the §6 eligibility re-check under the request row lock;
`read` scope on every list and detail; `delegation.write` scope including "OWN may only set myself
as delegator"; `policy.write` floored at ORG; 404-not-403 on unreadable detail ids so request ids
cannot be probed; and — new in the amendment — **an approver whose own hierarchy node is `exited`
cannot decide through any route** (ADR-0006 §6, §9). That last one is invisible to the client: an
exited approver holds a valid membership and token, sees the control, and is refused at the server.
The 403 state's copy must therefore not imply the caller did something wrong (§2.5).

**None of these grants exist in a live tenant until the ADR-0007 reconcile has run — see §2.8.**

### 2.5 Error mapping — problem+json to screen

`ApiError` carries `status` and the parsed `problem` (`type`, `title`, `status`, `detail`,
`instance`, `request_id`, and `errors[]` for 422). The mapping is uniform:

| Status / `type` | Where it renders | Heading | Body |
|---|---|---|---|
| 400/422 `validation` | on the field named in `errors[].field`; a summary above the form if no field matches | — | `errors[].message`, verbatim |
| 422 `business-rule` | inline above the form's actions | — | `problem.detail`, verbatim |
| 402 `product-not-entitled` | whole-screen state | "HR isn't on this plan" | "This workspace's plan doesn't include HR. An owner can add it from the OpsGrids dashboard." |
| 403 `forbidden` | whole-screen or inline at the action | "You can't do this" | `problem.detail` if present, else the screen's fallback sentence |
| 404 `not-found` | whole-screen state | "That request isn't available to you" | "It may have been removed, or it may belong to someone else's chain." |
| 409 `conflict` | inline, replacing the action row | "Already decided" | see §4.B stale/conflict |
| 429 `rate-limited` | inline at the action | "Too many attempts" | "Wait a moment and try again." |
| 5xx / `upstream-unavailable` | `ErrorState` | "Something went wrong" | `problem.detail`, plus "Try again" |
| network failure, `navigator.onLine === false` | `ErrorState` | "You're offline" | "Reconnect and try again. Nothing you typed has been lost." |

Rules: never retry a 4xx. Never clear a form on failure. `request_id`, when present, renders as
small muted text under the message: `Reference: 7f3c1a2e` — support asks for it, users never have
to find it. `ErrorState` needs an optional `title` prop to carry these headings (gap **G8**).

### 2.6 Loading, empty, pagination

- **Loading** is always a `Skeleton` layout matching the resolved layout's geometry — never a
  spinner, because every layout here is predictable. Row counts: inbox 8, my requests 6, delegations
  3, chain 3, history 4.
- **Empty** is always `EmptyState` with an icon, a title, a sentence, and — wherever an action
  exists — the action attached.
- **Pagination** follows the pattern already in `Employees.tsx`: server pages of **25**
  (`?page=&size=`, the platform's `PageParams`), appended by a full-width **"Load more · {n} left"**
  `Button variant="dark"`, with a closing line **"Showing all {n} requests"** when exhausted. The
  count line above the list always reads from the server's `total`, never from the loaded length.
  Filter and sort changes reset to page 1 and clear the accumulated pages. No infinite scroll: an
  approver needs to reach the end of a queue and know they did.
- **Large volumes** are the design target: an org of 4,000 employees, an HR admin whose unassigned
  queue is 300 rows, a department head with 60 open requests, an attendance table of 4,000 rows for
  one day. Every list here is server-paged and server-filtered; nothing sorts or filters 4,000 rows
  in the browser. The inbox's default sort is **oldest waiting first**, because the oldest is the
  one about to breach.

### 2.7 Announcements and focus

Every screen mounts one polite live region (`aria-live="polite"`, visually hidden) owned by the
screen, not by each control. It announces: a completed decision, a created or revoked delegation, a
submitted correction, a saved setting, and "Loaded 25 more requests" after "Load more". Errors that
replace a region use `role="alert"` (as `ErrorState` already does) and are not duplicated into the
polite region.

Focus rules are specified per screen. The general rule: after an action removes the element that had
focus, focus moves to the nearest stable heading and the change is announced — never to `<body>`.

### 2.8 The operational precondition: nothing here works until the reconcile has run

**This is the one thing that can make every screen in this document return 403 while the code is
perfectly correct**, so it is stated first and stated plainly.

Approval's permissions are declared in a catalogue, but a permission added to a role template that
already exists reaches **new organizations only** — a live tenant's `MEMBER`, `ORG_ADMIN` and
`ORG_OWNER` keep the grants they were born with, forever, with no error anywhere
([ADR-0007](../adr/0007-permission-rollout-to-live-tenants.md) Context). ADR-0006 §11 depends on
ADR-0007 to close that gap.

**The deploy order is load-bearing and the UI is the last step** (ADR-0007 §3):

1. Merge the catalogue change, regenerate the manifests.
2. Deploy `approval` (and the attendance change).
3. `tools/reconcile_permissions.py core --all` **and** `hrms --all` — `--dry-run` first. Both
   reconciles belong in the same deploy: `core` carries the four `core.approval.*` permissions,
   `hrms` carries `hrms.time.attendance.request` without which "Request a correction" 403s.
4. **Only then turn on the approvals UI.**

**What an organization that has not been reconciled sees**, and why it is worth naming rather than
treating as impossible:

| Surface | Behaviour before the reconcile |
|---|---|
| Every `/api/v1/approvals/*` call | **403 to every principal**, including the org owner |
| The unassigned queue | has **no holder anywhere** — nobody holds `decide:ORG` |
| "Request a correction" on Attendance | 403 — `hrms.time.attendance.request` is not granted |
| A brand-new organization | **works perfectly**, which is the worst way for this to present |

So the screens render it honestly instead of mysteriously. The §2.5 403 state is used, with copy that
names the situation and does not blame the user:

> **"Approvals aren't switched on here yet"**
> "Your organization's roles don't include approvals. An owner can check with OpsGrids support."

**Three design rules follow, and they are not negotiable:**

- **Never hide the nav group on a 403.** Hiding it makes a deploy-order mistake invisible and turns a
  five-minute fix into a support ticket. The entry stays; the screen explains.
- **Never treat a 403 on approvals as "this feature does not exist here".** ADR-0007 §4 leaves
  customer-authored roles untouched by design, so a tenant that replaced `MEMBER` with a role of
  their own has *some* members who can use approvals and some who cannot, in the same organization,
  with no pattern a user can see. The state is **per principal**, not per workspace, and the copy says
  "your role", not "this workspace". (The operator's number for this is ADR-0007 §4's
  `memberships_without`; it is not a UI surface.)
- **This is never a plan-gated state.** `/api/v1/approvals` has no `product` key (ADR-0006 §1, §14),
  so it can never return 402. A 403 here is a grant problem, never a billing one, and the copy must
  not suggest buying anything.

**One session-level consequence.** The reconcile bumps `pv` per organization, which invalidates every
access token in that tenant (ADR-0007 §5) — a refresh burst at the moment it runs. A user signed in
at that instant is refreshed transparently by `@opsgrids/client`'s existing auth; if the refresh
fails they land on sign-in. No new screen, but the expected behaviour is stated so a burst of
sign-ins during a deploy is recognised rather than investigated.

### 2.9 The requester's reason (`request_note`) — who may see it, and how it is shown

**Changed by the amendment.** The requester's free text is **no longer part of `summary`**. `summary`
now holds only structured, enumerable facts (for a correction: `date`, `from`, `to` — every value a
date or a member of a closed vocabulary). The free text lives in its own column, `request_note`,
≤500 characters (ADR-0006 §3).

**Its classification, in the ADR's own terms:** customer content authored by the requester about
their own working day. **It is not a sealed PII field**, deliberately — the same text already sits in
plaintext in `attendance.regularization_requests.reason`, and sealing it would put one audit row on
every inbox render, which floods the trail PII auditing depends on. *A control that must be bypassed
to render a list is not a control.*

**What this means for the interface, as rules an engineer can check:**

- **Render it as ordinary text.** No mask, no blur, no "Reveal" control, no unmask affordance
  anywhere. Inventing one would advertise a control that does not exist and would imply an audit
  trail that is not written.
- **Who the server gives it to** (ADR-0006 §3, rule 3, as widened): the requester, **anyone who
  appears on a step of the request — any outcome, so anyone currently eligible *and* anyone who was
  asked earlier** — or a holder of `read:ORG`. Nobody else, on any surface. The client does not
  filter it: **if the field is present, the caller is allowed to see it.**
- **Which endpoints carry it (C7, resolved):** the **inbox list** and the **request detail**, and
  nothing else. It is deliberately **not** on `GET /requests` — the requester's own list, where
  nothing renders it (they wrote it). Triage is the inbox's whole purpose, and an approver who must
  open 25 rows to find the one they can answer will instead approve on the summary line alone. The
  volume is bounded: 500 characters capped by attendance's own CHECK, so a 25-row page carries
  ~12 KB.
- **If it is absent from a response, render nothing at all** — no empty quote block, no "hidden"
  placeholder, no lock icon. An absent field is not a state to decorate.
- **Where it must never go, and these are client-side obligations:** never into a URL or query
  parameter, never into `localStorage` or `sessionStorage` (so an in-progress correction draft is
  held in memory only and is lost on reload — which is acceptable, and is why §4.C never promises to
  restore a draft), never into a console log, and never into any client error report or telemetry
  payload alongside the `request_id` line. The ADR keeps it out of events and audit rows; the client
  keeps it out of everywhere a browser would otherwise leak it.
- **How it is shown:** full text, wrapped, never truncated on the request detail (§4.B). Truncated to
  one line with the full text in `title` in a list (§4.A). It is the requester's own sentence, so it
  is never rewritten, prefixed, or summarised by the interface.

### 2.10 A decision can be made and still not land

**New in the amendment, and this document did not previously cover it.** ADR-0006 §12 adds a return
event: the product reports back whether it applied the decision, and approval records
`application_state` (`applied` / `failed`), `applied_at` and `application_failure_reason`.

**The event means "I have applied your decision", not "I corrected the day"** — and this is the
sentence that makes the whole section legible. Attendance publishes it for **every** decision it
consumes, so a **rejection is reported too**, and a rejection that lands is `applied`: the row moved
to `rejected`, which is the faithful application of that decision. `applied` therefore never means
"the day was corrected"; it means "your answer reached the record". Both outcomes have both
endings, and the interface must render four combinations rather than three.

A decided request therefore has **three** facts, not one, and confusing them is the failure this
section exists to prevent:

| Fact | Values | What it is |
|---|---|---|
| `status` | `approved` / `rejected` | **Approval's answer.** Final — first decision wins, and there is no re-decision and no undo |
| `application_state` | `NULL` / `applied` / `failed` | **Whether the product moved its row.** A fact *about* the decision, never a revision of it |
| The attendance row's own status | `present` / `half_day` / `absent` / `regularized` | **The product truth**, and what the record screen shows (ADR-0006 Consequences) |

**What each person sees, in each case:**

| `status` | `application_state` | The requester sees | The approver / past participant sees |
|---|---|---|---|
| `approved` | `NULL`, just decided | `Approved` | `Approved` — the decision succeeded; nothing is pending for them |
| `approved` | `applied` | `Approved`, and the attendance record reads `Regularised` | `Approved` |
| `approved` | `failed` | **`Approved — not applied`** + the reason + what to do | **`Approved — not applied`** + the reason |
| `rejected` | `NULL`, just decided | `Rejected` + the decider's comment | `Rejected` |
| `rejected` | `applied` | `Rejected` + the comment. **No application line** — the record faithfully reads "correction rejected", which is what the requester already sees | `Rejected` |
| `rejected` | `failed` | **`Rejected — not applied`** + the reason. The day is untouched *and* the record never recorded the refusal | **`Rejected — not applied`** + the reason |

**Rules:**

- **`applied` is silent; only `failed` is ever shown.** A decision that landed needs no line — the
  request's own status already says what happened, and the record agrees with it. The application
  line exists solely to flag a contradiction between the two.
- **`rejected` + `failed` is rare and worth naming anyway.** It means attendance could not move its
  own row to `rejected` — so the correction sits `pending` on the record while the request reads
  `Rejected`, and the employee could otherwise be left waiting on an answer that has already been
  given. Copy: **"Rejected, but the record wasn't updated."** / `{application_failure_reason}` /
  **"The day is unchanged. Ask HR to close the request on the record."**
- **No invented "applying…" state.** Between the decision and the report there is one relay hop and
  one consumer hop — seconds. A transitional spinner for a gap the user will not see costs more than
  it explains. **`NULL` never means "something went wrong" on its own** (ADR-0006 §12): for those
  seconds, and for any product that does not report back at all, it means "not yet told". The
  operator's probe is time-based for exactly that reason, and the interface says nothing until it
  has something to say. Screens refetch on window focus; that is the whole mechanism.
- **Never "partially approved", never "approval failed".** The approval did not fail. The words are
  **"Approved"** and **"the correction couldn't be applied"**, in that order, in that sentence.
- **The action keeps its name.** A button that said `Approve` produces `Approved` — the failure copy
  is appended to that word, never substituted for it.
- **There is no retry control**, because the ADR provides no re-apply endpoint. The copy points at
  the escape hatch the ADR does designate: the direct attendance override, available to `write:ORG`
  holders. For anyone else the instruction is to contact HR.
- **Where the fields travel (C8, resolved):** `application_state` and `application_failure_reason`
  are on the **request detail** *and* on **`GET /requests`** — the requester's own list, which is
  exactly where the person who asked will look. They are deliberately **not** on the inbox: an inbox
  holds pending requests, and a pending request has no decision to apply, so the field would be
  `null` on every row forever.

### 2.11 No due dates, no countdowns, and no overdue styling before slice 4

**Changed by the amendment (ADR-0006 §10).** Slices 2 and 3 open **every** step with
`due_at = NULL`, and the SLA sweep does not run until slice 4. This is deliberate staging, not a
temporary shortcut: a `due_at` stamped in slice 2 that nothing can act on would make every request
raised in the intervening weeks an overdue open step and would make the platform's *oldest overdue
open step* alarm fire continuously and correctly — which is how a team learns to ignore an alarm
permanently.

**Therefore, in slices 2 and 3, none of the following exists anywhere in the interface:**

- the inbox's **Due** column, its sort, and any due or overdue value on a row;
- the **overdue banner** and the **`Overdue`** filter chip;
- the due line on the request detail's "Waiting on" card;
- overdue styling of any kind — no `text-negative`, no `AlertTriangleIcon`, no red, no bold;
- the words **"Due"**, **"Overdue"**, **"in 6 hours"**, **"No due time"** and **"SLA"**;
- the `escalated_to_me` / `escalated_away` chips and the escalation history sentences (nothing
  escalates without a deadline);
- the **Approval settings** screen and its nav entry (§4.G), which configures a sweep that is not
  running.

**The single engineering rule that makes this safe: the client never computes a due time.** It
renders `due_at` when the server sends one and renders nothing when it does not. It never derives a
deadline from `opened_at` plus an assumed SLA, never falls back to a default of 1440 minutes, and
never treats a null `due_at` as "unknown, show a placeholder". Null means *there is no deadline* —
full stop. An engineer who adds a default here re-creates exactly the alarm storm the ADR staged
around.

**Null `due_at` does not go away in slice 4.** It stays legal and stays common, for two reasons the
UI must both handle: escalation is switched off for that request type (`enabled = false`), and steps
opened *before* slice 4 keep `due_at = NULL` forever and never escalate (a policy edit applies only
to steps opened after it). So the slice-4 Due cell renders **`No due time`** for nulls and sorts them
last — and an inbox in the weeks after slice 4 legitimately shows a mix.

**The inbox is fully usable without any of this**, which is why the staging works: its default sort
is **oldest waiting first**, computed from how long the request has been waiting, not from a
deadline. That sort is unchanged across all four slices.

---

## 3. User flows

Each flow lists entry, steps, decisions, exits and **every failure branch**.

### 3.1 Employee requests an attendance correction

1. **Entry:** `/attendance`, a row the employee owns (their own `employee_id`), status `absent`,
   `half_day` or `present`.
2. Activate **"Request a correction"** on that row.
3. Dialog: choose **"Change to"** (only statuses legal from the record's current status), type a
   **reason** (required, non-blank, ≤500 characters — this becomes `request_note`, §2.9).
4. Submit → `POST /api/v1/attendance/{id}/regularization-requests`.
5. **Exit (success):** dialog closes, row shows **"Correction pending · Waiting on Priya Nair"**,
   announced.
6. Decision branches — note that **approved and applied are two separate events** (§2.10):
   - **Approved, then applied** → the attendance row's own status becomes **Regularised** (product
     truth, ADR-0006 §12). The row shows "Corrected on 15 Sep".
   - **Approved, not yet reported** (`application_state` NULL, the seconds between the two hops) →
     the row still reads **"Correction pending"**, because the record has not moved. No "applying…"
     state is invented; the screen refetches on focus.
   - **Approved, but it couldn't be applied** (`application_state = 'failed'`) → row shows
     **"Correction couldn't be applied"** with `failure_reason` — the case where HR changed the day
     directly in the meantime. The request itself still reads **Approved**: the decision stands and
     is never re-opened. There is no retry control; the copy points at HR.
   - **Rejected** → row keeps its status and shows **"Correction rejected"** with the decider's
     comment when there is one.
- **Failure branches:**
  - Another correction is already pending on that record → the action is not offered; if raced, the
    server returns 409 and the dialog shows "There's already a correction waiting on this day."
  - The chosen change isn't legal from the current status → 422 `business-rule`, rendered under the
    "Change to" field; the field's options are refetched from the record.
  - Reason too short → 422 `validation` on the reason field; input preserved.
  - Record no longer exists or isn't the caller's → 404 → dialog closes, the table refetches, and a
    message says "That attendance record isn't available any more."
  - HR plan lapsed → 402 → whole-screen plan-gated state.
  - Offline → submit is blocked, inline message, nothing typed is lost.
  - **Approval service is down or its worker is stopped** (the ADR's named quiet failure): the
    correction is created and sits at "Waiting to be routed" with no approver name. Copy: **"Waiting
    to be routed — this usually takes a few seconds."** After 10 minutes the same row reads
    **"Still waiting to be routed. Contact HR if this doesn't clear."** The screen never claims an
    approver it doesn't have.

### 3.2 Approver decides

1. **Entry:** sidebar "Waiting on me", or a deep link.
2. Scan the inbox: who asked, what they want, since when, why it's mine — **and, from slice 4 only,
   due or overdue** (§2.11).
3. Open the request → detail with the full history.
4. **Approve** or **Reject** → confirmation dialog naming the person, the date and the consequence,
   with an optional comment.
5. Confirm → `POST /api/v1/approvals/requests/{id}/decision`.
6. **Exit:** back to the inbox, the row is gone, announced: "Approved Ananya Iyer's attendance
   correction for 12 Sep."
- **Failure branches:**
  - **Someone else decided first** → 409. The dialog closes, the detail reloads decided and
    read-only, and an inline "Already decided" message names the decider and the instant (§4.B).
  - **Eligibility lost** (the delegation that put it in my inbox was revoked while I read it) → 403
    with the server's detail; the actions are replaced by the message; the row disappears on the
    next inbox load.
  - **I am the requester** (possible for a `decide:ORG` holder looking at their own request) → the
    actions are never rendered, and the server refuses regardless.
  - **Request not readable** → 404 whole-screen state.
  - Network or 5xx → the dialog stays open, the comment is preserved, an inline error with "Try
    again".
  - Offline → the confirm button is blocked with an inline message; nothing is lost.
  - **The decision is made but the product can't apply it** (§2.10) → nothing happens in this flow.
    The approver's work is complete at step 6; the failure surfaces later, on the request detail, as
    **"Approved — the correction couldn't be applied"**. It is never reported to the approver as a
    failure of their action, because their action succeeded.
    **The approver can go back and see it.** The detail read rule now admits anyone who appears on a
    step of the request, any outcome (ADR-0006 §11, **O8 resolved**), so the person who decided keeps
    access to what they decided — including its applied/failed outcome and the requester's reason.
    An earlier draft of this spec said that access was doubtful; it is not. The link from "My
    requests", from the inbox history, or from a bookmark all resolve for them.

### 3.3 Approver hands over (delegation)

1. **Entry:** "Delegations".
2. **"Set up a delegation"** → search for a colleague in the picker (now served by
   `GET /api/v1/approvals/delegate-candidates?q=`, **O3 resolved**), set start and end, confirm.
3. `POST /api/v1/approvals/delegations`. On success the list shows it as **Active** or
   **Scheduled**, and — when the delegation starts immediately — a note says how many open requests
   moved, from the granted `repointed_count` (§4.E).
- **Failure branches:** overlapping delegation → 409 with "You already have a delegation that covers
  those dates."; delegate is yourself → excluded by the endpoint itself and refused server-side;
  delegate has left → 422 with the server's detail (exited employees are not returned by the picker,
  so this is the raced case); end before start → client validation on blur; end in the past → 422;
  the query is shorter than two characters → the picker's short-query state, not a request; the
  search matches nobody → the picker's no-results state.
4. **Revoke** → confirm naming the delegate and the consequence → `POST /delegations/{id}/revoke`.
   Already revoked → 409, the row refreshes to Revoked.

### 3.4 HR handles an unassigned request

1. **Entry:** inbox, "Unassigned" filter (visible only when the caller receives unassigned rows).
2. Open → the detail says why it is unassigned (the reason recorded when it opened).
3. Approve or Reject as in §3.2. The same 409/403 branches apply.
- HR **cannot** decide an *assigned* request: `decide:ORG` covers unassigned only (ADR-0006 §6). The
  detail for an assigned request that HR can read shows no decision controls and a line: **"Only
  {Name} can decide this. To correct the day directly, use the attendance record."**

### 3.5 Nobody answers (escalation, slice 4 only)

**This flow does not exist in slices 2 and 3.** Steps open with no deadline and the sweep does not
run, so nothing escalates (§2.11).

From slice 4: no user action. The request appears in the next approver's inbox with
`Escalated to you from {Name}` and a fresh due time; it leaves the original approver's "Assigned to
me" view but stays visible under "Still mine" with the `Still yours — now with {Name}` chip (they
remain eligible — escalation widens who can answer and never removes the person asked first), and the
history records the move. When the chain is exhausted the request becomes unassigned and stops
escalating. Steps that opened before slice 4 have no deadline and never enter this flow at all.

### 3.6 HR sets the SLA (slice 4 only)

**The settings screen does not exist before slice 4** — it configures a sweep that is not running.

Settings → change the time for "Attendance correction" → Save → announced. Turning escalation off
shows the plain-language consequence inline **before** saving (§4.G). Failure: 403 if the caller
lacks `policy.write:ORG`; 422 on a value outside the server's `15 … 525600` minute range, on the
field. The lower bound is a database CHECK, not an API nicety (ADR-0006 §8, §10), so the client's
validation mirrors it rather than inventing its own.

### 3.7 Anyone checks who approves a person's requests

Employee page → "Approval chain". Read-only. Failure: 404 if the caller may not read that employee;
chain exhausted renders the HR fallback node; a cycle or depth cap renders the "chain couldn't be
resolved" state (§4.F).

---

## 4. Screen specifications

Shared shell for every screen: `AppShell` + product `Sidebar` (existing), `PageHeader` for the title
block, content in `Card` surfaces on the grey ground, 4px spacing scale, `tnum` on every column of
digits. Breakpoints used throughout: **mobile < 640**, **tablet 640–1023**, **desktop ≥ 1024** (the
sidebar rail appears at `lg`, matching `AppShell`).

---

### 4.A Approvals inbox — `/approvals`

**Purpose:** the one place an approver answers *what is waiting on me, and which is most urgent*.

#### Layout regions

1. **Header** — `PageHeader` eyebrow **"Waiting on you"**, title **"Approvals"**, actions: the count
   chip (same treatment as the Employees screen's people chip) reading `{n} waiting`.
2. **Overdue banner — slice 4 only** (§2.11). Before slice 4 this region does not exist and no space
   is reserved for it. From slice 4, and only when `overdue_count > 0`: an inline alert (gap **G7**)
   above the controls reading **"{n} requests are overdue."** with a **"Show overdue"** filter action.
3. **Controls** — a `Card` holding: a `SearchInput` (label "Search requests", placeholder "Search by
   person or reason…") and a row of `Pill` filters.

   | Filter | Ships in | Matches |
   |---|---|---|
   | `All` | 2 | everything the inbox returns |
   | `Assigned to me` | 2 | `inbox_reason = assigned` |
   | `Unassigned` | 2 | `inbox_reason = unassigned` |
   | `Delegated to me` | 3 | `inbox_reason = delegated_to_me` |
   | `Still mine` | 3 | `delegated_away` **or** `escalated_away` |
   | `Escalated to me` | 4 | `inbox_reason = escalated_to_me` |
   | `Overdue` | 4 | `due_at < now` |

   - `Unassigned` renders only if the caller has received at least one unassigned row (§2.4 rule 1).
   - **`Still mine` deliberately covers both "away" reasons with one filter**, even though the chips
     distinguish them (§2.3). The question a user asks is *what can I still answer*, not *how did it
     leave me*; two filters answering one question is decoration. The chip on the row carries the
     distinction for anyone who wants it.
   - A filter is never rendered before its slice — an empty filter for a state that cannot yet occur
     teaches users the product is broken.
   - Filters are server-side query params; changing one resets paging.
   - Search covers the requester's name and their **reason** (`request_note`). Searching the note is
     consistent with §2.9: the inbox only ever returns requests the caller is eligible on, and
     eligibility is exactly what entitles them to the note.
4. **List** — `DataTable`, caption "Requests waiting on you".
5. **Load more** — §2.6.

#### Columns (desktop), with progressive hiding

| Column | Content | Ships in | Hidden below |
|---|---|---|---|
| Requester | PersonRef **block** — avatar + `requester_name` over **`employee_no`** (§2.2) | 2 | — |
| Request | **"Attendance correction"** over the summary line built from `summary`: `12 Sep · Absent → Regularised` | 2 | — |
| Reason | `request_note`, truncated to one line with the full text in `title` (§2.9). **Confirmed on the inbox list response** (C7) — the column stays | 2 | `lg` |
| Waiting since | `2 days ago`, absolute in `title` | 2 | `sm` |
| Why | the §2.3 chip from `inbox_reason` | 2 | `md` |
| Due | `Due in 6 hours` / **`Overdue by 6 hours`** in `text-negative` with an `AlertTriangleIcon`; **`No due time`** when `due_at` is null | **4** | — |

**The requester's department sub-line is gone, and `employee_no` takes that line instead.** ADR-0006
§14 names the change against this section: `department_id` was dropped from the hierarchy copy
because it had no consumer. The second line is not empty, though — the amendment puts `employee_no`
on the **requester** of every inbox item precisely because this is the screen where confusing two
people has consequences (§2.2). So the sub-line now reads the employee number, which disambiguates,
rather than a department, which merely described. No placeholder, no dash, no client-side lookup.

**The Due column does not exist before slice 4** (§2.11). Its absence is not an empty column: the
table has five columns in slices 2–3 and six from slice 4, and the widths are specified for both.

Everything hidden on smaller screens must remain reachable: at `< 640` the row renders requester and
request summary (plus due, from slice 4), and the detail screen carries the rest. The `Why` chip
moves under the requester name at `< 768` so the reason for possession is never lost — it is the
field that tells an approver whether to act at all.

**Row activation** opens `/approvals/requests/:id` (`DataTable.onRowClick`, already keyboard-operable
via Enter/Space). **There are no per-row approve/reject buttons.** One decision path, one dialog, one
implementation; the detail is one keystroke away and shows the history a decision should be made
against. This is the deliberate "reduce steps before decorating steps" call: a per-row decision would
duplicate the comment field, the confirmation and all six failure branches.

#### Sort

Default **oldest waiting first**, computed from how long the request has been waiting — **not** from a
deadline, which is why the inbox is fully usable in slices 2–3 with no due dates at all (§2.11).

Sortable headers: **Waiting since** (slices 2+), **Due** (slice 4+, nulls sorted last so a step with
no deadline never displaces one that has one). Sort is server-side; `DataTable` already renders
`aria-sort`.

#### States

| State | What renders |
|---|---|
| **Loading** | Header and controls in place; 8 skeleton rows at row height, no layout shift |
| **Empty (no filters)** | `EmptyState`, `InboxIcon`. Title **"Nothing is waiting on you"**. Body **"When someone asks you to approve something, it lands here. You'll also see anything delegated or escalated to you."** No action |
| **Empty (filtered)** | Title **"No requests match this filter"**, body **"Try another filter or clear the search."**, action `Button variant="outline"` **"Clear filters"** |
| **Partial** | List renders; any row whose `requester_name` came back null uses the PersonRef fallback and stays operable (§2.2). A row whose `inbox_reason` is unrecognised renders no chip. If the locations lookup failed, times fall back to the organization zone and the header chip carries `title="Showing organization time"` |
| **Error** | `ErrorState` inside the list `Card`, `problem.detail`, action **"Try again"**. Controls stay usable |
| **No permission** | Every member holds `decide:OWN` **once the reconcile has run**, so a 403 here means either a per-principal grant gap or an un-reconciled organization (§2.8). Whole-screen: **"Approvals aren't switched on here yet"** / "Your role doesn't include approvals. An owner can check with OpsGrids support." The nav entry stays visible — hiding it on a 403 would make a deploy-order mistake invisible |
| **Plan-gated** | Not applicable: `/api/v1/approvals` is a platform route with no `product` key (ADR-0006 §1, §14), so it can never return 402. Never render a plan state here, and never suggest buying anything on a 403 |
| **Offline** | `ErrorState` "You're offline" with "Try again"; previously loaded pages stay on screen |
| **Stale / conflict** | A row decided by someone else simply isn't returned on the next load. After returning from a 409 on the detail, the inbox refetches and announces **"That request was already decided. Your inbox is up to date."** |
| **Loaded, small** | 1–5 rows; no "Load more"; closing line "Showing all 3 requests" |
| **Loaded, large (slices 2–3)** | 300+ rows: 25 per page, count chip reads `312 waiting`, **no overdue banner and no Due column**, "Load more · 287 left". The oldest-waiting-first sort is what makes 312 rows triageable without deadlines. Search and filters are server-side; sort never re-sorts only the loaded window |
| **Loaded, large (slice 4)** | As above, plus the overdue banner leading and the Due column. An inbox in the weeks after slice 4 legitimately shows a **mix** of rows with and without a due time — steps opened before slice 4 keep `No due time` forever (§2.11). That mix is expected and needs no explanatory copy |
| **Nothing routed yet** | Rows exist in "My requests" but not here because the approval worker is stopped (§3.1, **O6**). The inbox shows its ordinary empty state — it is not the inbox's job to report a worker outage, and it has no signal to do so |

#### Copy (final)

- Title: `Approvals` · Eyebrow: `Waiting on you` · Count chip: `{n} waiting`
- Filters (slices 2–3): `All`, `Assigned to me`, `Unassigned`, and from slice 3 `Delegated to me`,
  `Still mine`
- Filters (slice 4 adds): `Overdue`, `Escalated to me`
- Search: label `Search requests`, placeholder `Search by person or reason…`
- Column headers (slices 2–3): `Requester`, `Request`, `Reason`, `Waiting since`, `Why`
- Column headers (slice 4 adds): `Due`
- Summary line: `12 Sep · Absent → Regularised`
- Chips: `Assigned to you` · `Delegated from {Name}` · `Escalated to you from {Name}` ·
  `Still yours — {Name} is standing in` · `Still yours — now with {Name}` ·
  `Unassigned — no approver found`
- Load more: `Load more · {n} left` · Closing: `Showing all {n} requests`
- **Slice 4 only:** Overdue banner `{n} requests are overdue.` / action `Show overdue`;
  Due values `Due in 6 hours` / `Overdue by 6 hours` / `No due time`
- No-permission state: `Approvals aren't switched on here yet` /
  `Your role doesn't include approvals. An owner can check with OpsGrids support.`

#### Accessibility

- The table is a real `<table>` with a `<caption>` (sr-only) and `aria-sort` on sorted headers.
- Rows are operable by Enter and Space (existing `DataTable` behaviour); focus ring is visible on the
  focused row.
- "Load more" keeps focus on itself and announces **"Loaded 25 more. Showing 50 of 312."**; when the
  last page loads, focus moves to the closing line, which is the element that replaces the button.
- The `Why` chip is plain text inside the row, not a `title`-only affordance — it is read in the row's
  accessible name, because it is the field that decides whether the approver acts.
- **Slice 4 only:** the overdue banner is **not** a live region on first load (it would announce on
  every visit); it gets `role="status"` only when it appears as a result of a refresh. The overdue
  state never relies on red alone: the words "Overdue by" carry it, with an icon.
- Returning from the detail restores focus to the row that was opened, matched by request id; if that
  row is gone (just decided), focus goes to the list heading and the decision is announced.

---

### 4.B Request detail — `/approvals/requests/:requestId`

**Purpose:** everything needed to decide, plus the honest history of how the request got here.

#### Layout regions

Desktop: two columns, 2fr / 1fr. Tablet and mobile: one column, order as listed.

1. **Back link** — `ArrowLeftIcon` + **"Back to approvals"** (or "Back to my requests" when arrived
   from `/approvals/mine`). A real `<a>`.
2. **Header** — `PageHeader` eyebrow **"Attendance correction"**, title = `requester_name`, and a
   status pill: `Waiting on {Name}` / `Unassigned` / `Approved` / **`Approved — not applied`** /
   `Rejected` / **`Rejected — not applied`**. The two qualified values are new (§2.10): each
   qualifies its decision, never replaces it, and both are toned as a warning rather than a
   negative — the decision succeeded, only the record didn't move.
3. **What's being asked** (`Card`, left column) — a definition list:
   - `Employee` — PersonRef block
   - `Date` — `12 Sep 2026` *(from `summary.date`)*
   - `Change` — `Absent → Regularised` *(from `summary.from` / `summary.to`)* — an arrow between two
     `Tag`s, with the words readable in sequence to a screen reader
   - `Reason` — **`request_note`** (§2.9): the requester's full text, wrapped, never truncated, never
     rewritten or summarised. Rendered as plain text with no mask and no reveal control. **If the
     field is absent from the response, the whole `Reason` row is omitted** — no empty quote block,
     no "hidden" placeholder
   - `Raised` — `14 Sep, 9:12 am IST · 2 days ago`
4. **Decision panel** (`Card`, left column, only when the caller may decide) — see below.
5. **Waiting on** (`Card`, right column) — the current approver as PersonRef block and the §2.3 chip.
   **From slice 4 only**, a due line beneath: `Due 16 Sep, 9:12 am IST · in 6 hours`, or
   `Overdue by 6 hours`, or `No due time` when `due_at` is null. In slices 2–3 the card carries no
   due line and no space is reserved for one (§2.11).
6. **Outcome note** (inline, left column, only when `application_state = 'failed'`) — see §2.10 and
   the states table.
6. **History** (`Card`, right column on desktop, below on mobile) — the timeline.

#### The history timeline

Rendered as an ordered list, newest last (chronological, so it reads as a story), using the
`RecentActivity` pattern already in this app (tone dot + sentence + timestamp) — promoted to
`@opsgrids/ui` as `Timeline` (gap **G6**), because a second product will need it the moment a second
request type ships.

One sentence per entry, in the product's voice. `{t}` is always absolute + relative:

| Event | Sentence |
|---|---|
| opened, assigned | `Sent to Priya Nair for approval.` |
| opened, unassigned | `No approver was found in Ananya's reporting line, so this went to HR.` |
| opened, delegated | `Sent to Rohan Mehta, standing in for Priya Nair.` |
| delegated (re-pointed) | `Moved to Rohan Mehta, standing in for Priya Nair.` |
| escalated (to a person) | `Nobody answered in time, so this moved up to Marcus Feng.` |
| escalated (chain exhausted) | `Nobody answered in time and there was no one further up, so this went to HR.` |
| decided, approve | `Priya Nair approved this.` + the comment as a quoted block when present |
| decided, approve by a delegate | `Rohan Mehta approved this, standing in for Priya Nair.` |
| decided, reject | `Priya Nair rejected this.` + comment |
| **applied**, after an approval | `Ananya's attendance was corrected.` |
| **applied**, after a rejection | *no entry* — the decision entry above already says it, and "the rejection was applied" is system language for a fact the user has already read |
| **failed**, after an approval | `The correction couldn't be applied: {application_failure_reason}` |
| **failed**, after a rejection | `The record wasn't updated: {application_failure_reason}` |

**The last two entries are not steps.** `approval_steps` is append-only history of *routing*, and
application is a fact about the decision recorded on the request itself (ADR-0006 §12). The timeline
derives these two entries from `application_state`, `applied_at` and `application_failure_reason` on
the detail response, and renders nothing at all while `application_state` is null — there is no
"waiting to be applied" entry (§2.10).

Tone dots: neutral for routing, `positive` for approved, `late` for escalated, `negative` for
rejected — with the sentence carrying the meaning, never the dot alone.

A long history (an escalation ladder can reach 32 rungs) collapses after 6 entries behind
**"Show all 14 events"**, expanding in place; the most recent 6 are always visible. Expansion moves
focus to the first newly revealed entry.

#### Decision panel

Rendered only when the request is pending **and** the caller may decide. Contains:

- Heading `Your decision`
- `Button variant="primary"` **"Approve"**
- `Button variant="outline"` **"Reject"**
- A muted line: `You're deciding for Priya Nair.` (only when acting via a delegation)

Activating either opens a **confirmation dialog** (gap **G2**) — decisions are final; ADR-0006 gives
no cancellation and no re-decision, so there is no undo and the dialog says so.

**Approve dialog**
- Title: `Approve this correction?`
- Body: `Ananya Iyer's attendance for 12 Sep will change from Absent to Regularised. This can't be undone.`
- Field: `Comment (optional)` — multiline, 500 characters, helper `The requester can see this.`
- Actions: `Approve` (primary) · `Cancel` (ghost)

**Reject dialog**
- Title: `Reject this correction?`
- Body: `Ananya Iyer's attendance for 12 Sep stays Absent. This can't be undone.`
- Field: `Comment (optional)` — multiline, helper `Tell Ananya why. She'll see this on her request.`
- Actions: `Reject` (danger) · `Cancel` (ghost)

**On the comment being optional — now a server rule, not a UI choice.** The amendment settles **O1**
in §7: the comment stays optional for **both** outcomes, *stated as a server rule*, with the ADR's
own reasoning — either the server requires it or nobody does, and it chooses nobody, because an
approver who rejected "you already took this as leave" in person is a legitimate flow and a mandatory
field there produces `"."` rather than information.

So the UI **must not** make a rejection comment mandatory, and this is no longer a judgment this
document is making on its own. The pressure belongs in the nudge, which the ADR explicitly endorses:
the dialog **autofocuses the comment field on reject** and its helper says the requester will see it.
Making it mandatory later is a one-line server change plus a schema note, and it would be an
amendment to ADR-0006 §7 — at which point this screen follows in the same slice.

The action keeps its name through the flow: the button says **Approve**, the dialog's primary says
**Approve**, the announcement says **Approved**.

#### States

| State | What renders |
|---|---|
| **Loading** | Back link and header skeleton, then a skeleton definition list (5 rows) and 4 timeline skeletons. Two-column geometry is reserved so nothing shifts |
| **Empty** | Not applicable — a request always has content. A request with an empty history is impossible (opening writes one entry); if the history array is empty, render `Nothing has happened yet.` rather than a blank card |
| **Partial** | Request loads, names come back null → PersonRef fallbacks, decision still available. `request_note` absent → the `Reason` row is omitted entirely. History call fails → the history card shows its own `ErrorState` with "Try again" and the decision panel stays usable: *the decision must not depend on the history rendering* |
| **Approved, not yet reported** (`approved`, `application_state` null) | Status pill `Approved`. **No outcome note, no spinner, no "applying…"** (§2.10). The screen refetches on window focus |
| **Approved but not applied** (`approved` + `failed`) | Status pill **`Approved — not applied`** (warning tone). An inline alert (**G7**) where the decision panel was: **"Approved, but the correction couldn't be applied."** / body `{application_failure_reason}` / then **"The day wasn't changed. Ask HR to correct it directly."** — and for a caller holding `hrms.time.attendance.write:ORG`, instead: **"You can correct the day directly on the attendance record."** with a link to it. No retry control: the ADR provides no re-apply path. The timeline's last entry carries the same reason |
| **Rejected but not applied** (`rejected` + `failed`) | Status pill **`Rejected — not applied`** (warning tone). Inline alert: **"Rejected, but the record wasn't updated."** / `{application_failure_reason}` / **"The day is unchanged. Ask HR to close the request on the record."** The requester's attendance row still reads `Correction pending`, so this is the one case where the two screens disagree and the detail is the place that explains why |
| **Applied** (`approved` or `rejected` + `applied`) | The plain status pill, `Approved` or `Rejected`. For an approval the timeline's last entry reads `Ananya's attendance was corrected.`; for a rejection there is **no** application entry — the decision entry already said it. No banner in either case: success needs no announcement after the fact |
| **Error** | Whole-screen `ErrorState` with `problem.detail` and "Try again" |
| **No permission (read)** | 404 → **"That request isn't available to you"** / "It may have been removed, or it may belong to someone else's chain." + `Button` "Back to approvals". Deliberately indistinguishable from a wrong id (ADR-0006 §11). **It no longer applies to anyone who was part of the request** — see the row below |
| **Past participant** (decided it, was delegated it, was escalated past, or stood in) | **Full detail, read-only.** The read rule admits anyone appearing on a step of the request, **any outcome** (ADR-0006 §11, O8 resolved), so this is a first-class state, not an edge case. They see the requester's reason, the whole history, the decision and — the reason this matters — **the applied/failed outcome of the decision they made**. No decision panel (the request is not pending, or they are no longer eligible), and no explanatory line about why they can still see it: they were part of it, which is self-evident from the history they are reading |
| **No permission (decide)** | The request renders; the decision panel is **absent**, replaced by a muted line: `Only Priya Nair can decide this.` For an ORG reader looking at an assigned request, add: `To correct the day directly, use the attendance record.` For the requester's own request: `You can't decide your own request.` |
| **Plan-gated** | Not applicable (platform route) |
| **Offline** | The dialog's confirm is blocked with an inline `You're offline. Reconnect to send this decision.`; the comment is preserved |
| **Stale / conflict (409)** | The dialog closes, the detail refetches into its decided state, and an inline message sits where the decision panel was: **"Already decided — Priya Nair approved this on 15 Sep, 6:40 pm IST."** Announced politely. Focus moves to that message. The comment the caller typed is discarded *after* being shown in the message body as `Your comment wasn't saved.` when it was non-empty |
| **403 at decision** | Same placement: **"You can no longer decide this request."** + `problem.detail` (e.g. the delegation ended). The panel does not re-render |
| **Decided (normal)** | Status pill `Approved` / `Rejected`, no decision panel, the decision as the last timeline entry with its comment |
| **Decided by an approver who has since left** | Unchanged rendering — the decision stands and the name still resolves from the copy. An `exited` node affects who *may decide* (ADR-0006 §6), never what a past decision says |

#### Responsive

- **Desktop ≥1024:** two columns; history is sticky below "Waiting on" and scrolls independently only
  if it exceeds the viewport.
- **Tablet 640–1023:** single column, order: header → what's asked → decision → waiting on → history.
- **Mobile <640:** same order; the decision panel's two buttons go full-width stacked, Approve first;
  the dialog is a full-height sheet with the actions pinned to the bottom and the comment field above
  the fold when the keyboard is open.

#### Accessibility

- The dialog is a real modal: focus moves to the dialog's heading on open, is trapped inside, Escape
  cancels, and on close focus returns to the button that opened it — **unless** the decision
  succeeded, in which case the screen navigates back to the inbox and focus lands on the inbox
  heading with the announcement.
- After a decision on a screen that stays put (the ORG-reader case), focus moves to the status pill
  in the header, which is the element whose value changed.
- The live region announces exactly: `Approved Ananya Iyer's attendance correction for 12 Sep.` /
  `Rejected Ananya Iyer's attendance correction for 12 Sep.`
- `Absent → Regularised` is marked up so the arrow is decorative (`aria-hidden`) and the text reads
  `from Absent to Regularised`.
- The timeline is an `<ol>`; each entry's timestamp is a `<time datetime="…">` with the UTC instant
  in the attribute and the location-zone rendering as the text.
- The collapse control is a `<button aria-expanded>` labelled `Show all 14 events`.

---

### 4.C Request a correction — on the attendance record (`/attendance`)

**Purpose:** let an employee ask for a fix on their own day, from the row that is wrong.

This screen already exists. The changes are additive.

#### Changes to the Attendance screen

1. **Status vocabulary fix.** The service's statuses are `present | half_day | absent | regularized`
   (`services/attendance/app/domain.py`); the SPA's `AttendancePill` knows `present | late | absent`.
   They must be reconciled in slice 2 — see gap **G13**. Labels: `Present`, `Half day`, `Absent`,
   `Regularised`.
2. **A trailing "Correction" column** on the punches table, right-aligned, rendering one of the
   following from `AttendanceOut.regularization` (**C3**, granted — but note its field list below):
   - `Button size="sm" variant="outline"` **"Request a correction"** — only on the caller's **own**
     record (needs `subject_ref`, **C5**), when the status is not `Regularised` and no correction is
     pending. `allowed_transitions[]` drives the dialog's options.
   - `Tag` **"Correction pending"** with a second line `Waiting on {waiting_on_name}` — linked to the
     request when the caller may read it. When `waiting_on_name` is null the second line reads
     `Waiting to be routed` (§3.1, **O6**), never a blank or a guessed name.
   - `Tag` **"Correction rejected"** — with `comment` in `title` and in full on the request detail.
   - `Tag` **"Couldn't be applied"** — with `failure_reason`. This is the attendance-side view of
     §2.10's `application_state = 'failed'`: the request still reads **Approved**, and the day did
     not move.
   - Nothing, for everyone else's rows.

   **The requester's own reason is not shown on this row.** The granted `regularization` object is
   `{status, approval_request_id, requested_status, waiting_on_name, decided_at, comment,
   failure_reason}` — the ADR's §11 list does **not** include `reason`, which this spec had
   originally proposed. That is the right call and no workaround is needed: the note belongs to the
   request, the row already links to it, and the employee who typed it is the person looking at the
   row. Do not add a second copy of it here.
3. Below `md`, the column collapses into a line under the status pill so it survives at 360px.

#### The correction dialog

- Title: `Request a correction`
- Context line: `12 Sep 2026 · currently Absent`
- Field 1 — **Change to**: a radio group (not a select — there are at most three options and they
  must all be visible). Options are exactly the legal transitions from the record's current status
  (`ALLOWED_TRANSITIONS`): from `Absent` → `Present`, `Half day`, `Regularised`. Helper under the
  group: `Pick what the day should say.`
- Field 2 — **Reason** (required): multiline, label `Why does this need changing?`, helper
  `Your approver sees this. For example: "Worked from the Pune office, forgot to punch in."`
  Counter appears at 400 characters. **Validation mirrors the server exactly: non-blank, at most 500
  characters** — attendance stores it as `varchar(500)` with a non-blank CHECK, and it becomes
  `request_note` (§2.9).
  *Changed:* this spec previously imposed a **10-character minimum**. That is dropped. It was a
  client-only rule the server does not enforce, and ADR-0006 §7 rejects exactly that pattern for the
  reject comment — a required constraint only one client applies drifts the moment a second client
  exists. If a minimum length is wanted, it belongs in attendance's schema first.
- Actions: `Send request` (primary) · `Cancel` (ghost)
- Footer note: `This goes to your reporting manager for approval.`

Validation: on blur and on submit. Never clears input on failure. The reason field shows what a valid
value looks like via the helper example — the label labels, the example demonstrates.

#### States

| State | What renders |
|---|---|
| **Loading** | The attendance table's existing skeleton; the correction column skeletons with it |
| **Empty** | The existing "No punches for this day" empty state, unchanged |
| **Partial** | A row whose correction state didn't load renders no action rather than a wrong one, with `title="Correction status unavailable"` |
| **Error (dialog)** | Inline above the actions, from `problem.detail`; the form stays filled |
| **Plan-gated** | 402 on the attendance call → whole-screen `HR isn't on this plan` (§2.5). Note this is the one screen in this document where 402 is possible: `/api/v1/attendance` is an HRMS product route, unlike `/api/v1/approvals` (§2.8) |
| **Offline** | Submit blocked, inline `You're offline. Reconnect to send this request.`, input preserved |
| **Stale / conflict** | 409 → `There's already a correction waiting on this day.` inline; the dialog's actions become a single `Close`, and the table refetches behind it |
| **Illegal transition** | 422 `business-rule` under the radio group, verbatim from the server, options refetched |
| **Success** | Dialog closes; the row's cell becomes `Correction pending · Waiting on Priya Nair`; announced: `Correction requested for 12 Sep. Waiting on Priya Nair.` |
| **Success, not yet routed** | When the request has no approver yet (§3.1 failure branch): `Waiting to be routed` |
| **Approved and applied** | The row's own status becomes `Regularised` and the cell reads `Corrected on 15 Sep` |
| **Approved but not applied** | The row keeps its old status and the cell reads **`Couldn't be applied`** with `failure_reason` in `title` and in full on the request detail. The copy never says the approval failed (§2.10). For a caller with `hrms.time.attendance.write:ORG` the row's existing direct override is the fix; for anyone else the request detail tells them to ask HR |
| **Rejected and applied** | The cell reads `Correction rejected` with the decider's comment. This is the ordinary rejection path: `applied` means the record recorded the refusal, not that the day changed (§2.10) |
| **Rejected but not applied** | The cell still reads **`Correction pending`**, because attendance never moved its own row — and this is the one place where the record and the request disagree on screen. The request detail carries the explanation (§4.B). Do **not** paper over it by rendering `Correction rejected` from the approval status: the record is the product truth, and showing a rejection the record never took would hide exactly the failure the return event exists to expose |
| **No permission** | An employee without `hrms.time.attendance.request:OWN` never sees the button — including **every** employee in an organization that has not had `tools/reconcile_permissions.py hrms --all` run (§2.8). On the row it simply isn't rendered. If the caller reaches the endpoint anyway and gets 403, the dialog shows the §2.8 copy rather than a validation error |
| **Large volume** | An HR admin's attendance view for 4,000 employees on one day is server-paged like every other list; the correction column adds no client-side work per row |

#### Accessibility

- The radio group is a `<fieldset>` with a `<legend>` reading `Change to`.
- The reason field's helper and counter are tied with `aria-describedby`; the error message replaces
  neither, it is appended.
- Focus on dialog open: the first radio. On success: the row's correction cell, which now holds the
  status — announced via the screen's live region.
- The pending marker is text, not a colour; the `Tag` carries the words.

---

### 4.D My requests — `/approvals/mine`

**Purpose:** *what did I ask for, and what happened to it?*

#### Layout

1. `PageHeader` eyebrow **"Raised by you"**, title **"My requests"**.
2. Filter `Pill`s: `All` · `Waiting` · `Approved` · `Rejected`.
3. `DataTable`, caption "Requests you raised", newest first.

| Column | Content | Hidden below |
|---|---|---|
| Request | `Attendance correction` over `12 Sep · Absent → Regularised` | — |
| Status | `Waiting on {approver_name}` / `Waiting to be routed` / `Approved` / **`Approved — not applied`** / `Rejected` / **`Rejected — not applied`** pill | — |
| Raised | `14 Sep, 9:12 am IST` + relative | `sm` |
| Decided | `15 Sep, 6:40 pm IST` by PersonRef inline (`decided_by_name`), or `—` | `md` |

Row activation opens the same detail screen (§4.B), which renders without a decision panel for the
requester and shows the decider's comment.

**This list does show "Approved — not applied" (C8, resolved).** `application_state` and
`application_failure_reason` travel on `GET /requests` as well as the detail, on the ADR's own
reasoning: the person who asked is the one most worth telling, and their own list is where they will
look. The status cell therefore carries the qualified value directly, with the reason in `title` and
in full on the detail. The same applies to `Rejected — not applied` (§2.10).

This is the requester's earliest honest signal that a decision did not land — earlier than noticing
the attendance row never changed, which is the failure mode the return event exists to end.

#### States

| State | What renders |
|---|---|
| **Loading** | 6 skeleton rows |
| **Empty** | `EmptyState`, `ClipboardListIcon`. Title **"You haven't raised any requests"**. Body **"If an attendance record is wrong, open Attendance and ask for a correction."** Action: `Button` **"Go to attendance"** → `/attendance` |
| **Empty (filtered)** | **"No requests with this status"** / **"Try another filter."** + `Clear filters` |
| **Partial** | Decider name unresolved → PersonRef fallback; status still accurate |
| **Error** | `ErrorState` + `problem.detail` + "Try again" |
| **No permission** | Every member holds `read:OWN`, so this is a server failure, not a design case: render the §2.5 403 state |
| **Plan-gated** | Not applicable |
| **Offline** | "You're offline" `ErrorState` |
| **Stale** | A request decided since the page loaded shows its old status until refetch; the screen refetches on window focus, so a returning user sees the truth without a reload. No polling |
| **Loaded, large** | An employee with 400 historical corrections: 25 per page, "Load more · 375 left", closing count line |

#### Copy

- Title `My requests` · Eyebrow `Raised by you`
- Status values: `Waiting on {Name}`, `Waiting to be routed`, `Approved`, `Rejected`
- Empty: `You haven't raised any requests` / `If an attendance record is wrong, open Attendance and ask for a correction.` / `Go to attendance`
- **No due or overdue value appears on this screen in any slice.** A requester has no deadline to
  act on; the due time is the approver's, and surfacing it here would invite a requester to chase
  one (§2.11)

#### Accessibility

Same table rules as §4.A. The status column's value is text; the pill's colour is secondary. Focus
returns to the originating row on back-navigation.

---

### 4.E Delegations — `/approvals/delegations`

**Purpose:** hand my approvals to someone while I'm away, and take them back.

#### Layout

1. `PageHeader` eyebrow **"Standing in for you"**, title **"Delegations"**, action
   `Button variant="primary"` **"Set up a delegation"**.
2. **Form** (`Card`), shown when the action is used, inline above the list — not a dialog, because
   the date range needs room and the list is useful context while choosing.
3. **List** — one `Card` per delegation, or a `DataTable` at `md`+. Sections in order: **Active**,
   **Scheduled**, **Past** (revoked and expired), each with a `SectionHeader`.

#### The form

| Field | Control | Rules |
|---|---|---|
| Delegating for | **ORG mode only** — employee picker, defaults to me | Hidden entirely without `delegation.write:ORG` (§2.4). Label `Set this up for` |
| Delegate | employee picker (gap **G5**) over **`GET /api/v1/approvals/delegate-candidates?q=`** | Required. `q` is **required and at least 2 characters**; results are **capped at 20**. The endpoint already excludes the caller and every exited employee, so the picker filters nothing client-side |
| Starts | zone-aware datetime (gap **G10**) | Required. Defaults to the next hour boundary |
| Ends | zone-aware datetime | Required. Must be after Starts and in the future |

Under both datetime fields, one shared line: **"Times are in Bengaluru time (IST)."** — resolved from
**`delegator_location_code`**, which the amendment puts on delegation rows *and* on the create
response (§2.1). This line is not optional; without it the field is ambiguous and the ADR's SLA
arithmetic becomes unauditable to the user. When the code is `null` or unmatched, the line reads
**"Times are in organization time (Asia/Kolkata)."** — the fallback is always visible, never silent.

Helper under Delegate: `They'll be able to decide anything waiting on you while the delegation runs.`

**The delegate picker — resolved by the amendment (O3).** This spec recommended a narrow endpoint
rather than widening an HRMS employee scope, and the ADR adopted it: `GET /delegate-candidates?q=`
reads `hierarchy_nodes`, returns `{employee_id, display_name, employee_no}` and nothing else, needs
no new employee scope, and exposes no PII — a name and a staff number, to a colleague in the same
tenant. It is gated on `delegation.write` (any scope), so it is reachable only by someone who could
actually create a delegation. **The old "You can't look up colleagues yet" empty state is deleted**;
it described a hole that no longer exists.

Because the endpoint is search-first, the picker has three distinct pre-result states, and each
needs its own copy (see the states table): nothing typed, one character typed, and no matches.

Consequence note above the actions: **"Anything already waiting on you moves to them straight away.
The due times don't change."** (This is ADR-0006 §5's re-pointing rule, in plain words — it is
surprising and must be said before the user commits.) **Shown only when the delegation starts
immediately**: a future-dated delegation re-points nothing at creation (ADR-0006 §5), so the note
would be a false promise. For a future start, the note instead reads: **"This starts on 20 Sep.
Anything waiting on you now stays with you until then."**

Actions: `Set up delegation` (primary) · `Cancel` (ghost).

#### A delegation row

- `Rohan Mehta decides for you` (or `Rohan Mehta decides for Priya Nair` in ORG mode)
- `15 Sep, 9:00 am IST → 22 Sep, 9:00 am IST`
- A status `Tag`: `Active` · `Scheduled` · `Ended` · `Revoked`
- `Button size="sm" variant="outline"` **"Revoke"** on Active and Scheduled rows only

**The "Handling 4 requests right now" line is removed.** This spec asked for a per-row
`open_request_count` (**C4**) and the ADR refused it with a measured reason: it is a subquery per row
against the largest table in the service (47 ms at 2,986 pending) to render one line of copy. The
spec had already said the line is omitted when the field is absent, so the refusal costs nothing and
the line is now deleted rather than left as a permanent "if available". **Nothing is substituted for
it** — no client-side count, no estimate, no link that implies a count.

**Revoke confirmation** (dialog):
- Title: `Revoke this delegation?`
- Body: `Rohan Mehta will stop being able to decide for you immediately. Anything already with him
  stays with him and is still yours to decide. This can't be undone.`
- Actions: `Revoke` (danger) · `Keep it` (ghost)

That body is the ADR's §5 semantics stated honestly: revocation never re-routes, and the delegator
never lost eligibility. Saying "requests come back to you" would be a lie the history would
contradict.

#### States

| State | What renders |
|---|---|
| **Loading** | Header, then 3 skeleton rows |
| **Empty** | `EmptyState`, `ReplyIcon`. Title **"No delegations yet"**. Body **"Going away? Let a colleague decide your approvals while you're out."** Action **"Set up a delegation"** |
| **Empty (ORG mode, filtered to a person)** | **"{Name} has no delegations"** / **"You can set one up for them."** |
| **Partial** | A delegation whose delegate name didn't resolve renders with the PersonRef fallback and keeps its Revoke action |
| **Error** | `ErrorState` + detail + "Try again" |
| **No permission (create for others)** | The `Set this up for` field is absent; everything else works for the caller's own delegations |
| **No permission (any)** | Members all hold `delegation.write:OWN`; a 403 renders the §2.5 state |
| **Plan-gated** | Not applicable |
| **Offline** | Submit blocked inline, form preserved |
| **Stale / conflict** | Overlap 409 → inline above the actions: **"You already have a delegation that covers those dates."** with the conflicting range named from `problem.detail`. Revoking an already-revoked delegation → 409 → the row refreshes to `Revoked` and an inline note says **"This was already revoked."** |
| **Validation** | End before start → on blur, under Ends: **"End must be after the start."** End in the past → **"Pick a time in the future."** Delegate = delegator → the picker cannot select it; if raced, the server's 422 renders under Delegate |
| **Picker, nothing typed** | The listbox is closed and a helper reads **"Start typing a colleague's name."** No request is made |
| **Picker, one character** | **"Keep typing — at least two letters."** No request is made; the endpoint requires `q` ≥ 2 and a 422 for a state the UI can prevent is a bug |
| **Picker, no matches** | **"No one matches "{q}"."** / **"Check the spelling, or try their employee number."** — `employee_no` is searchable because the endpoint returns it |
| **Picker, 20 results** | The cap is the server's. When exactly 20 come back, a muted footer reads **"Showing the first 20. Keep typing to narrow it down."** — never a silent truncation |
| **Picker, error** | Inline in the listbox: **"Couldn't load colleagues."** + **"Try again"**. The rest of the form stays usable and the typed text is kept |
| **Success (create), starts now** | Form collapses, the new row appears first in Active, announced: **"Delegation set up. Rohan Mehta decides for you from 15 Sep, 9:00 am IST."** Then, from the granted **`repointed_count`**: `1` → **"1 request waiting on you moved to Rohan Mehta."**; `n > 1` → **"{n} requests waiting on you moved to Rohan Mehta."**; **`0` → the line is omitted entirely**, because "0 requests moved" is noise where silence is accurate |
| **Success (create), future start** | As above without any moved-requests line — a future-dated delegation re-points nothing (ADR-0006 §5). Announced: **"Delegation scheduled. Rohan Mehta decides for you from 20 Sep, 9:00 am IST."** |
| **Success (revoke)** | Row moves to Past with `Revoked`, announced: **"Delegation revoked. Rohan Mehta can no longer decide for you."** |
| **Loaded, large** | An HR admin in ORG mode sees every delegation in a 4,000-person org: server-paged 25 at a time, with a `SearchInput` (label `Search delegations`, placeholder `Search by person…`) and the same Load more pattern. Personal mode rarely exceeds a handful and is not paged differently — the same code path, the same 25 |

#### Accessibility

- The employee picker (**G5**, now backed by `GET /delegate-candidates?q=`) must be a proper
  combobox: `role="combobox"` with
  `aria-expanded`/`aria-controls`, a listbox of options, **Up/Down** to move, **Enter** to select,
  **Escape** to close and restore the typed text, **Home/End** to jump, and the active option tied
  by `aria-activedescendant`. Results are announced as `12 people match` via the picker's own polite
  region. Selecting sets a removable chip whose remove control is keyboard reachable and labelled
  `Remove Rohan Mehta`.
- The date range (**G10**) must be keyboard-complete without opening any calendar overlay: each field
  is a text input accepting a typed value, with the expected format in the helper
  (`Format: 15 Sep 2026, 9:00 am`). If a calendar popover is offered, **Escape** closes it, arrows
  move by day, PageUp/PageDown by month, and focus returns to the input on close. A date range that
  can only be set with a mouse is a defect.
- The two datetime fields are wrapped in a `<fieldset>` with `<legend>` `When it runs`, and the
  timezone line is referenced by both fields' `aria-describedby` so it is read with each.
- Focus after create: the new row's heading. After revoke: the row's status tag in its new section.

---

### 4.F Approval chain — section on `/employees/:employeeId`

**Purpose:** answer *who approves this person's requests, and in what order* — before anyone has to
ask why a request went where it went.

#### Host page (new, slice 1)

`/employees/:employeeId`, reached from the Employees grid card (the card becomes a link; the grid
otherwise stays as it is). Minimum content:

- Back link **"Back to employees"**
- `PageHeader` eyebrow = designation, title = full name, action = `EmployeeStatusPill`
- A `Card` repeating what the grid card shows (employee number, department, location, employment
  type, work mode) — no new data, no PII, no unmasking
- The **Approval chain** section specified below

Nothing else. This page exists in slice 1 to host the chain; growing it is a later decision.

#### The chain section

`Card` with `SectionHeader` title **"Approval chain"**, subtitle **"Who decides Ananya's requests,
in order."**

`GET /chain/{employee_id}` returns the rungs in order **with `display_name` and `employee_no` on
each**, **marks the rungs it skipped** because the node was `exited` (ADR-0006 §5), and carries
**`location_code` for the subject employee** so the section renders any instant in their zone
(§2.1). Every one of those facts comes from the server: the screen renders the chain it is given,
never re-derives it, and never calls the employees API to name or number a rung.

Rendered as an ordered list of nodes, each: a level marker (`1`, `2`, `3`), PersonRef block,
designation, and a status `Tag` where relevant:

```
1   Priya Nair          Engineering Manager
2   Marcus Feng         Operations Director
3   HR                  Anyone who can decide unassigned requests
```

- The **HR fallback** is always the last node, rendered with a `BuildingUserIcon` avatar substitute
  and the label **"HR"**, described as **"If nobody above can decide, the request goes to HR."**
- **Exited managers are not numbered rungs** — the walk returns every rung but chooses only
  non-exited ones (ADR-0006 §5), and the response marks the skipped ones. A muted line under the
  affected rung reads: **"Skipped Kabir Malhotra — no longer with the company."** so the chain's
  shape is explainable rather than mysterious. The skip flag is the server's; the client does not
  infer it from a status field.
- **The dotted-line manager** is shown, clearly separated, below the ordered list, in its own block:
  - Label: **"Dotted-line manager"**
  - PersonRef + designation
  - A muted sentence, mandatory: **"Not used for approvals. Requests follow the reporting line
    above."**
  - This block is omitted entirely when there is no dotted-line manager. It is never given a level
    number and never sits inside the ordered list — the numbering *is* the routing.
- A closing muted line: **"Requests keep the approver they were sent to, even if this chain changes
  later."** (ADR-0006 §9, in the user's words — it is the single most likely support question.)

#### States

| State | What renders |
|---|---|
| **Loading** | Section header, then 3 skeleton nodes at node height |
| **Empty (no manager at all)** | Inside the card: `EmptyState` (no icon) titled **"No approver in the reporting line"**, body **"Ananya has no reporting manager, so her requests go straight to HR."** The HR fallback node still renders |
| **Partial** | A rung whose employee record didn't resolve renders the PersonRef fallback and keeps its level number — the chain's shape is still true |
| **Error** | `ErrorState` inside the card, "Try again". The rest of the employee page is unaffected |
| **Chain couldn't be resolved** (cycle or depth cap, ADR-0006 §5) | Warning-toned inline alert: **"This chain couldn't be worked out — the reporting line loops back on itself. Requests from Ananya go to HR until it's fixed."** Plus, for a caller who can edit employees: **"Check the reporting manager on the people involved."** |
| **No permission** | A caller with `read:OWN` viewing someone else gets 404 on the employee page itself, so the section never renders alone. On the caller's own page the section always renders |
| **Plan-gated** | 402 on the employee call → whole-screen `HR isn't on this plan` |
| **Offline** | `ErrorState` "You're offline" inside the card |
| **Stale** | The chain is eventually consistent (ADR-0006 §2). No staleness indicator is shown — a few seconds' lag is not a state a user can act on. The page refetches on focus |
| **Loaded, large** | A 12-rung chain in a 4,000-person org renders in full; beyond 8 rungs the list collapses to the first 3 and last 2 with **"Show all 12 levels"**. The HR fallback is never collapsed |

#### Accessibility

- The chain is an `<ol>`; level numbers are real list semantics, not decorative badges.
- The dotted-line block is outside the list and introduced by its own `<h3>`, so a screen reader
  never hears it as a rung.
- "Show all 12 levels" is `<button aria-expanded>`; focus moves to the first revealed rung.
- The warning alert uses `role="status"` (it is present on load, not an interruption).

---

### 4.G Approval settings — `/approvals/settings`

**Purpose:** let HR set how long an approver gets, and decide what happens when nobody answers.

**Slice 4 only.** This screen, and its nav entry, do not exist before slice 4 — they configure a
sweep that is not running, over steps that have no deadline (§2.11). Shipping it earlier would let an
admin set a time limit that nothing enforces.

#### Layout

1. `PageHeader` eyebrow **"Approvals"**, title **"Approval settings"**, description
   **"How long approvers get, and what happens when nobody answers."**
2. One `Card` per request type. Today there is exactly one: **"Attendance correction"**. The card
   carries the plain name as its `SectionHeader` title and, as subtitle, **"Raised when an employee
   asks to fix their own attendance."**

#### Card contents

- **Field — "Time to decide"**: a number input with a unit select, or two `Pill`-style presets plus a
  custom value. Specified concretely: a number input labelled **"Time to decide"** with a unit
  control offering **hours** and **days**, defaulting to the stored `sla_minutes` expressed in the
  largest whole unit (1440 → `1 day`).
  **Its bounds mirror the server's CHECK exactly: `sla_minutes BETWEEN 15 AND 525600`** (15 minutes
  to 365 days), enforced in the database rather than by the API (ADR-0006 §8, §10). *Changed:* this
  spec previously capped the field at 30 days (`43200`), which is a client-only rule narrower than
  the server's — the same drift this document rejects elsewhere. The cap is now 365 days.
  Helper: **"How long each approver gets before the request moves up to their manager. Default: 1 day."**
- **Field — "Move requests up when nobody answers"**: a switch (gap **G4**), on by default.
- **The explanation, always visible under the switch** (not a tooltip, not only when off):
  - When **on**: **"If an approver doesn't answer in time, the request moves up to their manager.
    If there's nobody above, it goes to HR."**
  - When **off**: **"Requests stay with the approver until they answer. They never approve
    themselves, and nothing is decided automatically — a request with escalation off can wait
    indefinitely."**
  - The second sentence is mandatory and must not be softened. ADR-0006 §10 is explicit that
    disabling escalation never auto-approves, and the interface is where that promise is kept.
  - Turning the switch **off** additionally shows an inline confirmation before saving:
    **"Turn escalation off? Requests will wait for their approver with no time limit."** with
    `Turn it off` / `Keep escalation on`.
- **Effective-date note**, muted, under the fields: **"Changes apply to new requests. Anything
  already waiting keeps the due time it was given."** (ADR-0006 §10.)
- Actions: `Save changes` (primary, disabled until something changed) · `Discard` (ghost, shown only
  when dirty).

#### States

| State | What renders |
|---|---|
| **Loading** | Card with skeletons at field height; the labels render immediately (they are static) so the screen doesn't shift |
| **Empty** | Not applicable — a missing policy row means defaults (1 day, escalation on), and the form shows those with a muted note: **"Using the default settings."** |
| **Partial** | Policies load but one request type fails → that card shows its own error; others save normally |
| **Error (save)** | Inline above the actions, `problem.detail`. Values are preserved; nothing reverts |
| **No permission** | Nav entry hidden (§2.4). If reached by URL: whole-screen **"You can't change approval settings"** / **"Only an approvals administrator can change these. You can still see the current settings below."** followed by the **read-only** rendering — `GET /policies` is readable by anyone with `request.read`, so showing the values is both permitted and useful |
| **Read-only rendering** | Values as text, not disabled inputs: `Time to decide: 1 day` · `Requests move up when nobody answers: Yes` |
| **Plan-gated** | Not applicable (platform route) |
| **Offline** | Save blocked with inline `You're offline. Reconnect to save.`; values preserved |
| **Stale / conflict** | Two admins saving at once: last write wins server-side (`PUT`). After a successful save the form refetches; if the returned values differ from what was sent, an inline note says **"Someone else changed this at the same time. These are the settings now."** |
| **Validation** | Below 15 minutes → **"Give approvers at least 15 minutes."** Above 365 days → **"That's more than a year. Pick a shorter time."** Non-numeric → blocked by the input. Both bounds are the database's, not the screen's |
| **Existing requests are unaffected** | Stated in the effective-date note below, and true in a second way from slice 4: steps opened **before** slice 4 have no due time at all and never gain one, whatever is saved here (§2.11) |
| **Success** | Announced: **"Approval settings saved."** The `Save changes` button returns to disabled |
| **Loaded, large** | When more request types ship, each is a card in one column; beyond 8, the screen gains a `SearchInput`. Not built now |

#### Accessibility

- The switch is a real `<button role="switch" aria-checked>` with a visible label; its explanation is
  tied by `aria-describedby`, so the consequence is read with the control, not after it.
- The explanation text swaps on toggle inside an `aria-live="polite"` region — the meaning changes
  without a page change, which is exactly what a live region is for.
- The number input and the unit control are one labelled group (`<fieldset>`/`<legend>` `Time to
  decide`).
- Focus after save stays on `Save changes`; the announcement carries the result.

---

## 5. Action → endpoint → permission

The authority for this table is ADR-0006 §11 and the endpoint table in its §11, plus the attendance
changes in the brief. **Engineers must not infer a permission from what the UI renders.**

| # | Screen · action | Method · endpoint | Permission (guard) | Object rule the server applies |
|---|---|---|---|---|
| 1 | Inbox · load | `GET /api/v1/approvals/inbox` | `core.approval.request.decide` (any scope) | Pending requests where §6 holds for the caller; unassigned only with `:ORG`; the caller's own requests excluded |
| 2 | Inbox · filter/sort/page | same, query params | same | — |
| 3 | Detail · load | `GET /api/v1/approvals/requests/{id}` | `core.approval.request.read` | OWN: requester, **or anyone who appears on a step of this request** (`approver_employee_id` or `acting_for_employee_id`, **any outcome**), else **404**; ORG: any. Past participants keep access to what they decided |
| 4 | Detail · Approve | `POST /api/v1/approvals/requests/{id}/decision` `{outcome:"approve",comment?}` | `core.approval.request.decide` | §6 re-checked under the row lock. **403** not eligible · **409** already decided |
| 5 | Detail · Reject | same, `{outcome:"reject",comment?}` | `core.approval.request.decide` | as above |
| 6 | My requests · load | `GET /api/v1/approvals/requests` | `core.approval.request.read` | OWN = `requester_employee_id = sref` via `ctx.apply()`; ORG = all |
| 7 | Attendance · Request a correction | `POST /api/v1/attendance/{id}/regularization-requests` | `hrms.time.attendance.request` at OWN — **now declared** in ADR-0006 §11, `max_scope = OWN` (**O7 resolved**) | The caller's own record only; one pending per record. `max_scope` is OWN by design: "request a correction for everyone" is unrepresentable, not merely ungranted |
| 8 | Attendance · read records | `GET /api/v1/attendance`, `GET /api/v1/attendance/{id}` | `hrms.time.attendance.read` | `ctx.apply()` on the list; `assert_can_touch` on the detail |
| 9 | Attendance · HR direct override | `POST /api/v1/attendance/{id}/regularize` | `hrms.time.attendance.write`, **minimum ORG** | Existing endpoint; the escape hatch of ADR-0006 §6, not an approvals action |
| 10 | Delegations · load | `GET /api/v1/approvals/delegations` | `core.approval.delegation.write` | OWN: delegator or delegate is `sref`; ORG: all |
| 11 | Delegations · create (mine) | `POST /api/v1/approvals/delegations` | `core.approval.delegation.write` | OWN: `delegator_employee_id` omitted → `sref` |
| 12 | Delegations · create for another | same, with `delegator_employee_id` | `core.approval.delegation.write` at **ORG** | Any delegator |
| 13 | Delegations · revoke | `POST /api/v1/approvals/delegations/{id}/revoke` | `core.approval.delegation.write` | OWN: only the delegator; ORG: any |
| 14 | Delegations · delegate picker | **`GET /api/v1/approvals/delegate-candidates?q=`** | `core.approval.delegation.write` (any scope) | **New endpoint, O3 resolved.** Non-exited `hierarchy_nodes` in the caller's organization, excluding the caller. `q` required, ≥2 characters, capped at 20. Returns `{employee_id, display_name, employee_no}` only |
| 15 | Settings · load | `GET /api/v1/approvals/policies` | `core.approval.request.read` (any scope) | Read-only, org-wide |
| 16 | Settings · save | `PUT /api/v1/approvals/policies/{request_type}` | `core.approval.policy.write`, **minimum ORG** | — |
| 17 | Employee page · approval chain | `GET /api/v1/approvals/chain/{employee_id}` | `core.approval.request.read` | OWN: only `employee_id = sref`; ORG: any |
| 18 | Employee page · profile | `GET /api/v1/employees/{id}` | `hrms.people.employee.read` | `assert_can_touch` |
| 19 | Any screen · timezone resolution | `GET /api/v1/locations`, `GET /api/v1/organization` | platform routes, no product entitlement | Org-scoped reads; cached per session |

**Role templates** (ADR-0006 §11), for whoever configures the demo org: every member holds
`read:OWN`, `decide:OWN`, `delegation.write:OWN` through the `core` `MEMBER` template. ORG-level
approvals authority comes from the new **`APPROVAL_ADMIN`** core template, granted *alongside*
`HR_ADMIN` — HRMS's own template cannot grant `core.*`. `ORG_OWNER` holds all four at ORG, which is
what guarantees every organization has an owner for the unassigned queue.

> **None of those grants exist in a live tenant until the ADR-0007 reconcile has run** (§2.8).
> `install_registration` skips any role code that already exists, so a live tenant's `MEMBER`,
> `ORG_ADMIN` and `ORG_OWNER` keep the grants they were born with. The deploy that ships these
> screens must run **both** `tools/reconcile_permissions.py core --all` and `… hrms --all`
> (`--dry-run` first), *before* the UI is turned on. Until then every row in this table returns 403
> — in existing organizations only, while new ones work perfectly.
>
> `APPROVAL_ADMIN` is a **new** role code, so it does install into live tenants correctly — but
> **nobody holds it on day one**. An owner assigns it through the ordinary roles API. Configuring the
> demo org means doing that explicitly; it is not automatic.

**Never rendered because no permission can produce them:** an "approve anything" control for HR
(§6 forbids it), a cancel/withdraw control for a requester (deferred by the ADR), a re-route control
for an operator (deferred), a "decide for someone" control outside delegation.

---

## 6. Slice mapping

| Slice | Ships | Screens / parts |
|---|---|---|
| **0 — Precondition (not UI work)** | `tools/reconcile_permissions.py core --all` and `hrms --all` in the deploy that ships each slice's permissions, `--dry-run` first, **before** the UI is enabled | §2.8. Without it every screen below returns 403 in every existing organization |
| **1 — Hierarchy** | `/employees/:employeeId` host page; the **Approval chain** section with levels, HR fallback, dotted-line block, skipped-manager note, and the cycle state | §4.F in full; §2.1 timezone resolution; §2.2 PersonRef |
| **2 — Regularisation routed and decided** | The Approvals group in the sidebar (Waiting on me, My requests); the **inbox** with the `Assigned to you` and `Unassigned` chips only; the **request detail** with history and Approve/Reject; **Request a correction** on Attendance and every marker state on the record, including **"Couldn't be applied"**; **My requests**. **No due dates, no overdue anything** | §4.A, §4.B, §4.C, §4.D; §2.2, §2.3 (`assigned`, `unassigned`), §2.9, §2.10, §2.11; §2.5 error mapping; §2.7 announcements |
| **3 — Delegation** | **Delegations** nav entry and screen: create, list, revoke, the ORG "for another employee" mode, the delegate picker on `GET /delegate-candidates`, and the `repointed_count` copy; the `Delegated from {Name}` and `Still yours — {Name} is standing in` inbox chips and the `Delegated to me` / `Still mine` filters; `standing in for` sentences in the history and on the decision panel. **Still no due dates** | §4.E; the `delegated_to_me` / `delegated_away` rows of §2.3 and §4.B's history table |
| **4 — Escalation display + policy** | Everything §2.11 withholds, all at once: the **Due** column and its sort, the **overdue banner**, the **`Overdue`** filter, overdue styling, the `Escalated to you from {Name}` and `Still yours — now with {Name}` chips, the `Escalated to me` filter, the escalated history sentences, `No due time` for null due dates, and **Approval settings** with the escalation switch and its explanation | §4.G; the `escalated_to_me` / `escalated_away` rows of §2.3; the Due column, banner, filter and overdue states in §4.A; the due line in §4.B; escalation entries in §4.B's timeline |

Within slice 2, the inbox and the detail ship together — an inbox whose rows open nothing is not a
usable increment, and the decision lives only on the detail.

**Where the applied/failed display belongs — confirmed by the ADR (O10).** §2.10's states ship in
**slice 2**, with the decision itself, and the ADR now says so outright: the publisher is the same
transaction that applies the decision, the consumer is a few lines in approval, and *"a return path
that arrives two slices after the claim it qualifies is a bug with a schedule."* Both outcomes are
reported from slice 2, so `Rejected — not applied` ships alongside `Approved — not applied`.

---

## 7. What is deliberately not designed

Because ADR-0006 defers them, and designing them now would be guessing:

- **Notifications.** No bell, no email preview, no "notify approver" control. Approvers find work in
  the inbox; the SLA is what keeps it moving. The `count` on the nav entry is the only nudge.
- **Cancel / withdraw a request.** No control anywhere. If a requester asks, the answer is that HR
  can correct the day directly.
- **Re-routing a stuck request.** No operator control. The unassigned queue plus the product override
  is the path.
- **Amount limits, matrix/dotted-line routing, working-hours SLA.** Nothing in the UI hints at them.
- **A cross-product inbox switcher.** The inbox is cross-product in the backend from day one, but
  HRMS has one request type; a type filter with one option is decoration. Add it with the second
  type.

---

## 8. Design-system gaps

Everything below is missing from `@opsgrids/ui` or `@opsgrids/client` today. None of it may be
forked into `product-hrms` (ADR-0001). Ordered by what blocks the most.

| # | Gap | Needed by | What it must do |
|---|---|---|---|
| **G1** | **No permission hooks in `@opsgrids/client`** — no `usePermission`, no `RequirePermission`, no `useEntitlement`; `MeOut` exposes `roles`, not grants | Slices 3, 4 (§2.4) | `usePermission(code, minScope)` reading the access token's grants, mirroring the server's scope ladder. Until it exists, use the §2.4 fallback: hidden nav, server 403 rendered as the permission state |
| **G2** | **No `Dialog`** | Slice 2 | Modal with focus trap, Escape to close, restore focus on close, `aria-labelledby`/`aria-describedby`, full-height sheet below `sm`. Every confirmation in this document depends on it |
| **G3** | **No `TextArea`, `TextField`, `RadioGroup`, `Select`, or `FormField`** — `SearchInput` is the only input in the system | Slice 2 | `FormField` (label, helper, error, `aria-describedby` wiring, error never replacing the helper) plus the controls. Validation display is specified in §2.5 |
| **G4** | **No `Switch`** | Slice 4 | `role="switch"`, `aria-checked`, visible label, described-by consequence text |
| **G5** | **No employee picker / `Combobox`** | Slice 3 | Async search combobox with the keyboard contract in §4.E, a results live region, and a removable selection chip |
| **G6** | **No `Timeline`** | Slice 2 | The `RecentActivity` pattern generalised: `<ol>`, tone dot + sentence + `<time>`, collapse beyond N |
| **G7** | **No `InlineAlert` / `Banner`** | Slices 2, 4 | Info/warning/danger inline message with an optional action, `role="status"` or `role="alert"` by tone. Used for overdue, conflict and escalation-off copy |
| **G8** | **`ErrorState` has a hard-coded heading** ("Something went wrong") and no tone | Slice 2 | Optional `title` and `tone` props so 403/404/offline/plan states can use one component instead of five bespoke blocks |
| **G9** | **No timezone-aware formatting** — `product-hrms/src/lib/format.ts` hard-codes `timeZone: "UTC"` | Slice 1 | `formatInZone(instant, ianaZone, style)` and `formatRelative(instant, now)` in `@opsgrids/ui`'s `lib/format`, emitting the §2.1 formats with the zone abbreviation |
| **G10** | **No zone-aware datetime input** | Slice 3 | A datetime field that collects wall-clock and converts using a **named IANA zone**, never the browser's. A bare `<input type="datetime-local">` is browser-zone-naive and would silently violate ADR-0000 |
| **G11** | **No `Pagination` / load-more component** | Slice 2 | The `Employees.tsx` "Load more · {n} left" pattern, extracted with the announcement in §2.7. Otherwise a third screen re-implements it |
| **G12** | **`Sidebar` badge tone is a sidebar-wide prop**, not per item | Slice 4 (nice to have) | Per-`NavEntry` tone, so an overdue count can be red without recolouring Notifications. Avoided in this design rather than worked around |
| **G13** | **Attendance status vocabulary is wrong in the SPA** — `AttendancePill` knows `present \| late \| absent`; the service has `present \| half_day \| absent \| regularized` | Slice 2 | Reconcile to the service's four, labelled `Present`, `Half day`, `Absent`, `Regularised`. The correction flow cannot render its "Change to" options correctly until this is fixed |
| **G14** | **No toast/announcer utility** | Slice 2 | A per-screen polite live region helper (§2.7). Not a floating toast stack — confirmations here belong next to the thing that changed |

---

## 9. Contract dependencies and open questions

### 9.1 Resolved by the ADR-0006 amendment

All five contract dependencies this spec raised were granted, and three of its open questions were
settled in the ADR rather than in the UI. Recorded with their deltas, because two were granted in a
different shape than asked:

| # | Asked for | What the ADR granted |
|---|---|---|
| **C1** | Names on every approval response | **Granted in full.** `display_name` and `employee_no` on `hierarchy_nodes`, fed by the same full-state employee events; `requester_name`, `approver_name`, `acting_for_name`, `decided_by_name`, `delegator_name`, `delegate_name` on responses; an unresolvable name is `null`, not absent, and the client renders its own fallback (ADR-0006 §2, §11). `department_id` was **dropped** in the same amendment — see the delta below |
| **C2** | Why a request is in my inbox | **Granted in full.** `inbox_reason` per item with the six values, plus `from_name` (ADR-0006 §11). The ADR's reasoning matches this spec's: deriving it client-side means re-implementing §6 in the browser |
| **C3** | Correction state on the attendance record | **Granted, minus one field.** `AttendanceOut.regularization {status, approval_request_id, requested_status, waiting_on_name, decided_at, comment, failure_reason}` plus `allowed_transitions[]` — **`reason` is not included.** §4.C is updated to not show it there; the note belongs to the request, which the row links to |
| **C4** | `open_request_count` per delegation and `repointed_count` on create | **Half granted, deliberately.** `repointed_count` is free (already computed in that transaction) and is now specified copy in §4.E. `open_request_count` is **refused** with a measured cost — 47 ms per row at 2,986 pending, to render one line. The line is deleted, not deferred |
| **C5** | `subject_ref` on `MeOut` | **Granted**, named in ADR-0006 §11 as auth's contract dependency |
| **O1** | Is a rejection comment required? | **Settled as a server rule** in ADR-0006 §7: optional for both outcomes, for both this spec's reason and the ADR's own. The UI nudges; it does not require |
| **O3** | Where do delegate candidates come from? | **This spec's recommendation was adopted**: `GET /delegate-candidates?q=` on approval, from `hierarchy_nodes`, gated on `delegation.write`. The old empty state is deleted |
| **O7** | Is `hrms.time.attendance.request` real? | **Now declared** in ADR-0006 §11 with `max_scope = OWN`, and added to `EMPLOYEE` and `HR_ADMIN` at OWN |
| **O4** | Which timezone, and does the response carry the code? | **Granted — this had blocked slice 1.** `requester_location_code` on inbox items, `GET /requests` items and the detail; `delegator_location_code` on delegation rows and the create response; `location_code` for the subject on `GET /chain`. Not on `/policies` or `/delegate-candidates` (no instants). Approval returns the **code**; the SPA resolves it against its cached `/locations`. A `null` falls back to organization time and **must be visibly labelled** — never the browser's zone (§2.1) |
| **C6** | `employee_no` beyond delegate-candidates? | **Granted with a rule**, not blanket: it accompanies the person being *identified* — the **requester** (inbox, `GET /requests`, detail), each **chain rung**, each **candidate** — and not approver, delegate or decider names. §2.2 and §4.A follow; the inbox's requester cell uses the block variant and the number takes the line the department used to hold |
| **C7** | Is `request_note` on the inbox list or detail-only? | **Both, and nothing else.** The Reason column stays (§4.A). Explicitly **not** on `GET /requests`, where nothing renders it. §3's rule 3 is satisfied by construction — the inbox only returns requests the caller is eligible on |
| **C8** | Can "My requests" show "Approved — not applied"? | **Yes.** `application_state` and `application_failure_reason` travel on `GET /requests` as well as the detail. **Not** on the inbox — a pending request has no decision to apply, so the field would be null on every row forever (§4.D) |
| **O8** | Can a past decider still read the request? | **Yes, and it is now a first-class state.** The detail read rule admits anyone appearing on a step of the request, **any outcome**. §4.B's past-decider 404 is removed and replaced by a **Past participant** state; §3.2's doubt is corrected; §2.9's audience widens to match (ADR-0006 §3 rule 3) |
| **O9** | Is a rejection reported back? | **Yes.** The event means *"I have applied your decision"*, not *"I corrected the day"* — so a rejection that lands is `applied`. §2.10 now renders four combinations, including **`Rejected — not applied`**, which is the case where approval says rejected and the record still says pending |
| **O10** | Which slice publishes the return event? | **Slice 2**, stated in the ADR — *"a return path that arrives two slices after the claim it qualifies is a bug with a schedule."* §6's hedge is removed |

**One granted change removes a design element rather than adding one.** ADR-0006 §14 names §4.A
directly: `department_id` is dropped from the hierarchy copy, so **the inbox's department sub-line is
not available in these slices** and has been removed. It is not replaced by a client-side lookup —
approval would hold a bare `account.org_units` UUID it cannot render, and rendering it would need a
second denormalised name from a third service. Adding it later costs the same as adding it now.

### 9.2 Still open — what the ADR leaves undetermined for the UI

Every response-shape question this spec raised is now answered. What remains is one contract gap
outside approval's ownership, and three questions the ADR deliberately leaves to the interface.

| # | Question | How this spec proceeds |
|---|---|---|
| **C9** | **Which timezone does an attendance record render in?** Approval's responses now carry a location code for every instant they own (**O4**), but the **attendance** contract does not: the granted `AttendanceOut.regularization` object (**C3**) has no location code, and neither does `AttendanceOut` itself | §2.1 says the **record's** location governs a day and its punches, and that is unchanged — but nothing on the response names it, so §4.C currently renders dates (`12 Sep 2026`) rather than instants and sidesteps the question. **It has to be answered before attendance shows a punch time on the same screen as a correction**, or that screen will mix a record-zone date with an approval-zone timestamp. Smallest fix, matching what approval already does: a `location_code` on `AttendanceOut`, resolved by the same cached `/locations` lookup. Belongs to attendance's surface, not this document |
| **O2** | **Can HR see, but not decide, an assigned request?** Left to the interface | Render read-only with **"Only Priya Nair can decide this. To correct the day directly, use the attendance record."** The amendment adds one real exception: §6 widens `decide:ORG` to cover a step whose approver has **exited**, so the decision panel *does* appear in exactly that case |
| **O5** | **What does a requester see between a decision and the record changing?** | **Answered by §2.10 for every combination.** What stays a UI choice is the null window: no "applying…" state is invented for it, because `NULL` also means "this product does not report back yet" and the ADR is explicit that null never means something went wrong on its own |
| **O6** | **What does an approver see when the worker is stopped?** The ADR names this quiet failure, not its user-facing form | **"Waiting to be routed — this usually takes a few seconds."**, hardening after 10 minutes to **"Still waiting to be routed. Contact HR if this doesn't clear."** The UI never invents an approver it wasn't given |
