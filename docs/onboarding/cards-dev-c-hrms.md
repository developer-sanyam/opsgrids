# Dev C — the HRMS app

**You own `frontend/product-hrms/` and nothing else.**

Five cards, in this order. Each is roughly a day. Do not start a card until the previous one
is merged.

## What you are building, in plain language

`product-hrms` is the HR app a customer's staff actually open. Most of it already reads live
data from the backend. **The Attendance screen does not** — it shows invented sample rows, and
labels them "Sample data" so nobody mistakes them for real. Meanwhile a real attendance service
has existed for a while, with twelve working endpoints, and the gateway already knows how to
reach it.

Cards C1–C3 connect the screen to the real thing. Cards C4–C5 build two screens that today are
just a "coming soon" placeholder.

Read these once before card C1:

- `frontend/CLAUDE.md` — all of it. It is short, and it is the standard your work is measured against.
- `docs/onboarding/never-do.md`
- `docs/onboarding/git-workflow.md`

### Two rules from `frontend/CLAUDE.md` you will use on every card

**Four states, always.** Every list has a **loading** state that does not shift the layout when
it resolves, an **error** state that says what went wrong and how to fix it, an **empty** state
that says what to do next, and the **full** state. *"A screen that only has a full state is half
a screen."*

**TypeScript strict.** No `any`. No `!` to silence the compiler. If the type is wrong, fix the
type.

### How to run it

```bash
cd frontend && pnpm install                       # once
pnpm --filter @atmora/product-hrms dev            # http://localhost:4310
```

To see live data you need the backend running — `make infra`, then `make run SERVICE=auth
PORT=8110`, `make run SERVICE=account PORT=8111`, `make run SERVICE=attendance PORT=8121` and
`make run SERVICE=app-gateway PORT=8100`. Ask if that is not working; do not fight it alone.

---

# CARD C1 — the wire type

**BRANCH** `feat/c1-attendance-wire-type`

**GOAL** Describe, in TypeScript, exactly what the attendance service really returns.

### WHY THIS IS ITS OWN CARD

The app already has a type called `AttendanceRecord`, and its own comment admits it was
*"modelled from the plan"* rather than from the service. It is wrong in two ways that matter:
it contains `employee_no`, `full_name` and `department_id`, which the service does **not**
return, and the real endpoint is paginated while the fixture is a plain array.

You are adding a second, honest type. You are not deleting the old one — the next card uses
both.

### REFERENCE

- **The truth:** `services/attendance/app/schemas.py`, `class AttendanceOut` (lines 74–92).
- **The style:** the existing `EmployeeOut` interface in `frontend/product-hrms/src/api/types.ts`.

### FILES YOU WILL TOUCH

```
frontend/product-hrms/src/api/types.ts
```

### THE CONTRACT

Add one interface, named `AttendanceOut`, mirroring the backend field for field. Python types
map like this: `uuid.UUID` and `datetime` and `date` all become `string`; `X | None` becomes
`X | null`; `Decimal` becomes `string` (money and coordinates cross the wire as strings, never
as floating-point numbers).

```ts
export interface AttendanceOut {
  id: string;
  employee_id: string;
  work_date: string;
  check_in_at: string;
  check_out_at: string | null;
  source: string;
  location_code: string | null;
  shift_id: string | null;
  work_mode: string;
  status: string;
  check_in_lat: string | null;
  check_in_lng: string | null;
  check_out_lat: string | null;
  check_out_lng: string | null;
  notes: string | null;
  created_at: string;
}
```

Leave `AttendanceRecord` exactly where it is. Update its comment to say it is the **screen**
shape — what the table renders — as opposed to `AttendanceOut`, the **wire** shape.

### STEPS

1. Open the backend schema and the frontend types file side by side.
2. Add the interface. Check every field name character by character against the backend.
3. Nothing else. This card touches one file.

### DONE WHEN

```bash
cd frontend
pnpm --filter @atmora/product-hrms typecheck
pnpm --filter @atmora/product-hrms lint
pnpm --filter @atmora/product-hrms build
```

### DO NOT TOUCH

Anything outside `frontend/product-hrms/src/api/types.ts`. Especially not the backend.

---

# CARD C2 — the live call

**BRANCH** `feat/c2-attendance-live`

**GOAL** Make the Attendance screen read real attendance records instead of invented ones.

### REFERENCE

`getAllEmployees()` in `frontend/product-hrms/src/api/client.ts` — ten lines above the function
you are changing, and the exact two-branch shape every live call in this app uses:

```ts
export async function getAllEmployees(): Promise<Page<EmployeeOut>> {
  if (USE_FIXTURES) return settle({ items: EMPLOYEES, total: EMPLOYEES.length });
  return apiRequest<Page<EmployeeOut>>(`/employees?size=${FULL_PAGE}`);
}
```

### FILES YOU WILL TOUCH

```
frontend/product-hrms/src/api/client.ts
```

### THE CONTRACT

`listAttendance(workDate: string)` keeps its current signature and still returns
`Promise<AttendanceRecord[]>`, so the screen does not have to change.

Its body becomes:

1. **If `USE_FIXTURES`** — exactly what it does today. Do not change that branch.
2. **Otherwise:**
   - Call `apiRequest<Page<AttendanceOut>>(\`/attendance?date_from=${workDate}&date_to=${workDate}&size=${FULL_PAGE}\`)`.
     The endpoint is `GET /attendance`; `date_from` and `date_to` are both inclusive, so passing
     the same date on both gives you one day.
   - Call the existing `getAllEmployees()` to get the directory.
   - **Join them in the client**, by `employee_id` → `EmployeeOut.id`, to fill the three fields
     the service does not return: `employee_no`, `full_name`, `department_id`.
   - Return `AttendanceRecord[]`.

The join is necessary because attendance-service owns attendance and employee-service owns
people; no service reads another's tables, so a record that needs both is assembled by whoever
asked for both. That is the platform's design, not a workaround.

**If an attendance row's `employee_id` is in no employee in the directory**, keep the row and
use `"—"` for `full_name` and `employee_no`, and `null` for `department_id`. Do not drop the
row and do not throw. A missing name is a display problem; a dropped row is a payroll problem.

Run the two requests concurrently with `Promise.all`, not one after the other.

### STEPS

1. Read `getAllEmployees` and the two or three functions around it.
2. Change only `listAttendance`. Leave its fixture branch alone.
3. Delete the comment above it that says *"No attendance service on this org yet"* — that is no
   longer true and a stale comment is worse than none.
4. Check it in the browser both ways:
   `pnpm --filter @atmora/product-hrms dev` for live, and with `VITE_USE_FIXTURES=true` set for
   fixtures. Both must work.

### DONE WHEN

```bash
cd frontend
pnpm --filter @atmora/product-hrms typecheck
pnpm --filter @atmora/product-hrms lint
pnpm --filter @atmora/product-hrms test
pnpm --filter @atmora/product-hrms build
```

and, in the pull request, one sentence each on what you saw on the Attendance screen in live
mode and in fixture mode.

### DO NOT TOUCH

`routes/Attendance.tsx` — that is the next card. Any other app. Anything under `services/`.

### IF YOU GET STUCK

A `402` from the gateway means the organization has no `hrms` entitlement — that is a data
problem, not your code. A `404` means the attendance service is not running. Either way,
comment; do not start editing the gateway.

---

# CARD C3 — retire the sample label

**BRANCH** `feat/c3-attendance-not-sample`

**GOAL** Stop telling the user the attendance data is made up, now that it is not.

### REFERENCE

`ATTENDANCE_IS_SAMPLE` in `frontend/product-hrms/src/api/client.ts`, and every place it is used
— find them with:

```bash
cd frontend && grep -rn "ATTENDANCE_IS_SAMPLE" product-hrms/src
```

### FILES YOU WILL TOUCH

`frontend/product-hrms/src/api/client.ts`, `routes/Attendance.tsx`, and whichever dashboard
files the grep above turns up. List them in the pull request.

### THE CONTRACT

Remove the `ATTENDANCE_IS_SAMPLE` constant and every "Sample data" chip that depends on it —
**but only where the data is now genuinely live.**

Read each usage before deleting it. If a chip sits on a widget whose numbers are still derived
or invented rather than fetched, **leave that one and say so in the pull request.** Removing a
truthful warning is worse than leaving a stale one.

If the constant ends up unused entirely, delete it. If some usages survive, keep it and narrow
its comment to what is still sample.

### STEPS

1. Run the grep. Write the list down.
2. For each usage, decide from card C2's work whether that surface is live now.
3. Remove what is genuinely live. Leave and justify the rest.
4. Look at the screen in the browser. The layout must not jump or leave a gap where a chip was.

### DONE WHEN

```bash
cd frontend
pnpm --filter @atmora/product-hrms typecheck
pnpm --filter @atmora/product-hrms lint
pnpm --filter @atmora/product-hrms test
pnpm --filter @atmora/product-hrms build
grep -rn "ATTENDANCE_IS_SAMPLE" product-hrms/src     # every remaining hit is justified in the PR
```

---

# CARD C4 — the Departments screen

**BRANCH** `feat/c4-departments-screen`

**GOAL** Replace the "coming soon" placeholder at Departments with a real screen.

### REFERENCE

`frontend/product-crm/src/routes/Activities.tsx` — 89 lines, the simplest complete page in this
repository. Its skeleton is the recipe: a `PageHeader`, a filter row, then a `Card` that renders
loading / error / empty / full.

### FILES YOU WILL TOUCH

```
frontend/product-hrms/src/api/types.ts        ← one interface
frontend/product-hrms/src/api/client.ts       ← one function
frontend/product-hrms/src/routes/Departments.tsx   ← new file
frontend/product-hrms/src/main.tsx            ← one line, plus its import
frontend/product-hrms/src/ui/Sidebar.tsx      ← the nav entry may already exist; check first
```

### THE CONTRACT

**The endpoint** is `GET /org-units`, already routed by the gateway to account-service. It
returns `Page<OrgUnitOut>`. The wire shape, from
`services/account/app/schemas.py` lines 102–112:

```ts
export interface OrgUnitOut {
  id: string;
  name: string;
  code: string;
  parent_id: string | null;
  head_membership_id: string | null;
  kind: string;
  is_active: boolean;
}
```

**The client function:** `listOrgUnits(): Promise<OrgUnitOut[]>`, in the same two-branch shape
as every other function in that file. Add a small fixture array for the fixture branch — four
or five rows, plausible department names, at least one with a `parent_id` pointing at another
row and at least one with `is_active: false`.

**The screen** shows a table with columns **Name**, **Code**, **Kind**, **Status**. Sort by
name. Show inactive departments with a muted status pill — do not hide them.

The four states:

| State | What it says |
|---|---|
| loading | a skeleton the same height as a populated row, so the layout does not jump |
| error | what failed and what to do — *"Could not load departments. Check your connection and try again."* plus a retry control |
| empty | what to do next — *"No departments yet. An administrator can add them from the organization settings."* |
| full | the table |

Use components from `@atmora/ui` — `PageHeader`, `Card`, `DataTable`, `Pill`, and the loading
and error components in `States.tsx`. **Do not build your own versions of these, and do not
copy one into this app.**

### STEPS

1. Read `Activities.tsx` completely before writing anything.
2. Look at how `routes/Employees.tsx` in this same app fetches and renders — it is the closest
   working example of your four states in this codebase.
3. Types, then client, then screen, then the route line, then the nav entry.
4. Test all four states: with the backend up, with it down (error), and with an organization
   that has no departments (empty).

### DONE WHEN

```bash
cd frontend
pnpm --filter @atmora/product-hrms typecheck
pnpm --filter @atmora/product-hrms lint
pnpm --filter @atmora/product-hrms test
pnpm --filter @atmora/product-hrms build
```

and, in the pull request, a screenshot of each of the four states.

### DO NOT TOUCH

`frontend/atmora-ui/` — if you think a shared component needs a change, that is a different
card for a different person. Comment.

---

# CARD C5 — the Locations screen

**BRANCH** `feat/c5-locations-screen`

**GOAL** The same again, for Locations.

### REFERENCE

Your own card C4. This one exists so you do it a second time and it gets faster.

### THE CONTRACT

Endpoint `GET /locations`, also account-service, also `Page<...>`. Wire shape from
`services/account/app/schemas.py` lines 128–136:

```ts
export interface LocationOut {
  id: string;
  name: string;
  code: string;
  timezone: string;
  country: string | null;
  is_active: boolean;
}
```

Columns: **Name**, **Code**, **Timezone**, **Country**, **Status**. Everything else — the four
states, the shared components, the file list — is exactly as C4.

Show `country` as `—` when it is `null`. Never render the word "null" to a user.

### DONE WHEN

The same four commands, and the same four screenshots.

---

## After C5

This app routes **ten** screens to the "coming soon" placeholder — more than any other app in
the repository. Reports, Team Members, Tasks and Notifications are all cards of the same shape
as C4, and you will be the person who knows how to do them.
