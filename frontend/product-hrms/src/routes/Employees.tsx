import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { listEmployees } from "@/api/client";
import type { EmployeeOut, EmployeeStatus } from "@/api/types";
import { departmentLabel } from "@/data/departments";
import { useAsync } from "@/lib/useAsync";
import { employmentLabel } from "@/lib/format";
import {
  Avatar,
  Card,
  EmployeeStatusPill,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
  Tag,
} from "@/ui";
import { MapPinIcon, UsersIcon } from "@/ui/icons";

type StatusFilter = EmployeeStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "probation", label: "Probation" },
  { value: "on_notice", label: "On Notice" },
];

const PAGE_SIZE = 9;

/** A stable empty reference so memo deps don't churn while loading. */
const NO_EMPLOYEES: EmployeeOut[] = [];

function EmployeeCard({ emp }: { emp: EmployeeOut }) {
  return (
    <Card className="p-5 flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <Avatar name={emp.full_name} size="lg" />
        <div className="flex min-w-0 flex-1 flex-col leading-tight">
          <span className="truncate font-bold text-ink">{emp.full_name}</span>
          <span className="truncate text-sm text-muted">{emp.designation}</span>
          <span className="mt-0.5 text-xs text-faint tnum">{emp.employee_no}</span>
        </div>
        <EmployeeStatusPill status={emp.status} />
      </div>

      <div className="flex items-center gap-4 text-sm text-muted">
        <span className="inline-flex items-center gap-1.5">
          <UsersIcon className="size-4 text-faint" />
          {departmentLabel(emp)}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <MapPinIcon className="size-4 text-faint" />
          {emp.location_code}
        </span>
      </div>

      <div className="flex flex-wrap gap-2 border-t border-line pt-4">
        <Tag>{employmentLabel(emp.employment_type)}</Tag>
        <Tag className="capitalize">{emp.work_mode}</Tag>
      </div>
    </Card>
  );
}

export function EmployeesPage() {
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [group, setGroup] = useState<string | null>(null);

  // Seed the search from the ?q= param when arriving from the sidebar / dashboard search.
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [visible, setVisible] = useState(PAGE_SIZE);

  // Search and status filter server-side; the team/department is resolved and filtered client-side
  // (the live service stores an unresolved department UUID, so we group by the job-title label).
  const loader = useCallback(
    () => listEmployees({ search, status }),
    [search, status],
  );
  const employees = useAsync(loader, [search, status]);

  const resetWindow = () => setVisible(PAGE_SIZE);

  const items = useMemo(
    () => (employees.status === "success" ? employees.data.items : NO_EMPLOYEES),
    [employees],
  );

  // Distinct team labels present in the current result set, for the department chips.
  const groups = useMemo(() => {
    const seen = new Set<string>();
    for (const emp of items) {
      const label = departmentLabel(emp);
      if (label !== "—") seen.add(label);
    }
    return [...seen].sort((a, b) => a.localeCompare(b));
  }, [items]);

  const filtered = useMemo(
    () => (group ? items.filter((e) => departmentLabel(e) === group) : items),
    [items, group],
  );

  const shown = filtered.slice(0, visible);
  const total = items.length;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Directory"
        title="Employees"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <UsersIcon className="size-4 text-muted" />
            <span className="tnum">{total}</span>
            <span className="text-muted font-medium">people</span>
          </span>
        }
      />

      {/* Controls */}
      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search employees"
            placeholder="Search by name, ID or role…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              resetWindow();
            }}
            containerClassName="lg:max-w-xs"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STATUS_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={status === f.value}
                onClick={() => {
                  setStatus(f.value);
                  resetWindow();
                }}
              >
                {f.label}
              </Pill>
            ))}
          </div>
        </div>

        {groups.length > 0 ? (
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 border-t border-line pt-4">
            <span className="pr-1 text-xs font-semibold uppercase tracking-wide text-faint shrink-0">
              Team
            </span>
            <Pill
              active={group === null}
              onClick={() => {
                setGroup(null);
                resetWindow();
              }}
            >
              All
            </Pill>
            {groups.map((g) => (
              <Pill
                key={g}
                active={group === g}
                onClick={() => {
                  setGroup(g);
                  resetWindow();
                }}
              >
                {g}
              </Pill>
            ))}
          </div>
        ) : null}
      </Card>

      {/* Grid */}
      {employees.status === "loading" ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i} className="p-5 flex flex-col gap-4">
              <div className="flex gap-3">
                <Skeleton className="size-12" />
                <div className="flex-1 flex flex-col gap-2">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-24" />
                </div>
              </div>
              <Skeleton className="h-8 w-full rounded-tile" />
            </Card>
          ))}
        </div>
      ) : null}

      {employees.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={employees.error.message} />
        </Card>
      ) : null}

      {employees.status === "success" ? (
        filtered.length === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={<UsersIcon className="size-6" />}
              title="No matching employees"
              description="No one matches these filters. Try a different team, status, or clear the search."
            />
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
              {shown.map((emp) => (
                <EmployeeCard key={emp.id} emp={emp} />
              ))}
            </div>
            {visible < filtered.length ? (
              <div className="flex justify-center pt-2">
                <button
                  type="button"
                  onClick={() => setVisible((v) => v + PAGE_SIZE)}
                  className="rounded-full bg-ink px-6 h-11 text-sm font-semibold text-white hover:bg-ink-soft transition-colors"
                >
                  Load more · {filtered.length - visible} left
                </button>
              </div>
            ) : (
              <p className="text-center text-sm text-faint">
                Showing all {filtered.length} employees
              </p>
            )}
          </>
        )
      ) : null}
    </div>
  );
}
