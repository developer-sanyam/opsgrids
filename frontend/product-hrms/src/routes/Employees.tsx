import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { listEmployees } from "@/api/client";
import { getOrgUnitNameById } from "@/api/orgUnits";
import type { EmployeeOut, EmployeeStatus } from "@/api/types";
import { resolveDepartment } from "@/data/departments";
import { employeesEntity } from "@/data/importExport/employees";
import type { EmployeeFilters } from "@/data/importExport/employees";
import { Can, useAsync, useCan, useImportExport } from "@opsgrids/client";
import { employmentLabel } from "@/lib/format";
import {
  Avatar,
  Button,
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
import {
  ImportExportButton,
  LoadMore,
  MapPinIcon,
  PlusIcon,
  Select,
  SM_QUERY,
  UsersIcon,
  useMediaQuery,
  useToast,
  ChipRow,
} from "@opsgrids/ui";
import { EmployeeForm } from "@/ui/EmployeeForm";

type StatusFilter = EmployeeStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "probation", label: "Probation" },
  { value: "on_notice", label: "On Notice" },
  { value: "exited", label: "Exited" },
];

const PAGE_SIZE = 9;

function EmployeeCard({ emp, orgUnitMap }: { emp: EmployeeOut; orgUnitMap: ReadonlyMap<string, string> }) {
  return (
    <Link
      to={`/employees/${emp.id}`}
      className="block rounded-card transition-shadow hover:shadow-float focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
    >
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
            {resolveDepartment(emp, orgUnitMap)}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <MapPinIcon className="size-4 text-faint" />
            {emp.location_code ?? "—"}
          </span>
        </div>

        <div className="flex flex-wrap gap-2 border-t border-line pt-4">
          <Tag>{employmentLabel(emp.employment_type)}</Tag>
          <Tag className="capitalize">{emp.work_mode}</Tag>
        </div>
      </Card>
    </Link>
  );
}

export function EmployeesPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [departmentId, setDepartmentId] = useState<string>("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<EmployeeOut[]>([]);
  const [creating, setCreating] = useState(false);

  // Seed the search from the ?q= param when arriving from the sidebar / dashboard search.
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const orgUnits = useAsync(getOrgUnitNameById, []);
  const orgUnitMap = orgUnits.status === "success" ? orgUnits.data : new Map<string, string>();

  const query = useAsync(
    () => listEmployees({ q: search, status, departmentId: departmentId || undefined, page, size: PAGE_SIZE }),
    [search, status, departmentId, page],
  );

  // What an export of this list must respect: the filters on screen, not the page window.
  const exportFilters = useMemo<EmployeeFilters>(
    () => ({ q: search, status, departmentId: departmentId || undefined }),
    [search, status, departmentId],
  );
  // The window is appended one page at a time, so after an import it starts over from the first
  // page and asks again: the new employees show up without a refresh.
  const { reload: reloadList } = query;
  const reloadAfterImport = useCallback(() => {
    setPage(1);
    reloadList();
  }, [reloadList]);
  const dx = useImportExport(employeesEntity, { filters: exportFilters, onImported: reloadAfterImport });
  const canImport = useCan("hrms.people.employee.write", "ORG");
  // From 640px Import / Export is its own button; below it, an item in the header's "More actions"
  // menu that opens the same dialog.
  const roomy = useMediaQuery(SM_QUERY, true);
  const [ioOpen, setIoOpen] = useState(false);

  // Server paging, appended — `LoadMore`'s own contract. A filter change resets the window. Keyed
  // on the loaded page itself (`useAsync` returns a new object every render, and `page` moves a
  // render before its data does), so each page is added once, and the page it is added as is the
  // one the server says it is.
  const loaded = query.status === "success" ? query.data : null;
  useEffect(() => {
    if (loaded === null) return;
    setItems((prev) => (loaded.page === 1 ? loaded.items : [...prev, ...loaded.items]));
  }, [loaded]);

  function resetWindow() {
    setPage(1);
  }

  const total = query.status === "success" ? query.data.total : 0;
  const filtersActive = search !== "" || status !== "all" || departmentId !== "";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Directory"
        title="Employees"
        meta={
          <span className="inline-flex items-center gap-1.5">
            <UsersIcon className="size-4 text-muted" aria-hidden />
            <span className="tnum font-semibold text-ink">{total}</span> people
          </span>
        }
        secondaryActions={
          roomy ? undefined : [{ label: "Import / Export", onSelect: () => setIoOpen(true), hidden: !(canImport || dx.meta.canExport) }]
        }
        actions={
          <div className="flex flex-wrap items-center gap-2 [&_button]:whitespace-nowrap">
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canImport}
              canExport
              open={ioOpen}
              onOpenChange={setIoOpen}
              hideTrigger={!roomy}
            />
            <Can permission="hrms.people.employee.write" scope="ORG">
              <Button onClick={() => setCreating(true)} leadingIcon={<PlusIcon className="size-4" />}>
                Add employee
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search employees"
            placeholder="Search by name, ID or role…"
            value={search}
            onDebouncedChange={(next) => {
              setSearch(next);
              resetWindow();
            }}
            containerClassName="lg:max-w-xs"
          />
          <Select
            label="Department"
            hideLabel
            containerClassName="lg:w-56"
            value={departmentId}
            onChange={(e) => {
              setDepartmentId(e.target.value);
              resetWindow();
            }}
            options={[
              { value: "", label: "All departments" },
              ...[...orgUnitMap.entries()].map(([id, n]) => ({ value: id, label: n })),
            ]}
          />
          <ChipRow label="Filter employees by status">
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
          </ChipRow>
        </div>
      </Card>

      {query.status === "loading" && items.length === 0 ? (
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

      {query.status === "error" && items.length === 0 ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load employees"
            message={query.error.message}
            onRetry={() => query.reload()}
          />
        </Card>
      ) : null}

      {query.status !== "loading" || items.length > 0 ? (
        items.length === 0 && query.status === "success" ? (
          <Card className="p-6">
            <EmptyState
              icon={<UsersIcon className="size-6" />}
              title={filtersActive ? "No matching employees" : "No employees yet"}
              description={
                filtersActive
                  ? "No one matches these filters. Try a different team, status, or clear the search."
                  : "Add the first employee to start the directory."
              }
              action={
                filtersActive ? null : (
                  <Can permission="hrms.people.employee.write" scope="ORG">
                    <Button variant="dark" onClick={() => setCreating(true)}>
                      Add employee
                    </Button>
                  </Can>
                )
              }
            />
          </Card>
        ) : items.length > 0 ? (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
              {items.map((emp) => (
                <EmployeeCard key={emp.id} emp={emp} orgUnitMap={orgUnitMap} />
              ))}
            </div>
            <LoadMore
              loaded={items.length}
              total={total}
              noun="employees"
              loading={query.status === "loading"}
              onLoadMore={() => setPage((p) => p + 1)}
            />
          </>
        ) : null
      ) : null}

      {creating ? (
        <EmployeeForm
          employee={null}
          onClose={() => setCreating(false)}
          onSaved={(emp) => {
            toast.success(`${emp.full_name} created.`);
            navigate(`/employees/${emp.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
