import { useMemo, useState } from "react";

import type { EmployeeOut } from "@/api/types";
import { departmentLabel } from "@/data/departments";
import {
  Avatar,
  DownloadIcon,
  EmptyState,
  FilterIcon,
  MapPinIcon,
  MoreHorizontalIcon,
  SearchIcon,
  SortIcon,
  UsersIcon,
  cn,
} from "@opsgrids/ui";
import { EmployeeStatusPill } from "./StatusPill";

/** Build a CSV of the given rows and hand it to the browser as a download. */
function exportCsv(rows: EmployeeOut[]): void {
  const header = ["Employee No", "Name", "Designation", "Department", "Status", "Location"];
  const body = rows.map((e) => [
    e.employee_no,
    e.full_name,
    e.designation ?? "",
    departmentLabel(e),
    e.status,
    e.location_code ?? "",
  ]);
  const csv = [header, ...body]
    .map((cells) => cells.map((c) => `"${c.replace(/"/g, '""')}"`).join(","))
    .join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "employees.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function ToolbarButton({
  icon,
  label,
  active = false,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-full border px-3 text-sm font-semibold transition-colors",
        active
          ? "border-accent bg-accent-soft text-accent-ink"
          : "border-line bg-surface text-ink-soft hover:bg-ground",
      )}
    >
      {icon}
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

/**
 * The live employees table: a search / filter / sort / export toolbar, row checkboxes with a
 * violet selected state, and a status pill per person. Every toolbar control does real work —
 * filter narrows to active staff, sort flips name order, export downloads the current rows.
 */
export function EmployeesPanel({ employees }: { employees: EmployeeOut[] }) {
  const [query, setQuery] = useState("");
  const [activeOnly, setActiveOnly] = useState(false);
  const [asc, setAsc] = useState(true);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return employees
      .filter((e) => {
        const matchesQuery =
          q === "" ||
          e.full_name.toLowerCase().includes(q) ||
          e.employee_no.toLowerCase().includes(q) ||
          (e.designation?.toLowerCase().includes(q) ?? false);
        return matchesQuery && (!activeOnly || e.status === "active");
      })
      .sort((a, b) =>
        asc
          ? a.full_name.localeCompare(b.full_name)
          : b.full_name.localeCompare(a.full_name),
      );
  }, [employees, query, activeOnly, asc]);

  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));

  function toggleRow(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)));
  }

  const exportRows = selected.size > 0 ? rows.filter((r) => selected.has(r.id)) : rows;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-full bg-ground px-3 focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 sm:max-w-xs">
          <SearchIcon className="size-[18px] shrink-0 text-faint" />
          <label htmlFor="emp-panel-search" className="sr-only">
            Search employees
          </label>
          <input
            id="emp-panel-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search employees…"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
        </div>
        <div className="flex items-center gap-2">
          <ToolbarButton
            icon={<FilterIcon className="size-4" />}
            label="Active only"
            active={activeOnly}
            onClick={() => setActiveOnly((v) => !v)}
          />
          <ToolbarButton
            icon={<SortIcon className="size-4" />}
            label={asc ? "A–Z" : "Z–A"}
            onClick={() => setAsc((v) => !v)}
          />
          <ToolbarButton
            icon={<DownloadIcon className="size-4" />}
            label="Export"
            onClick={() => exportCsv(exportRows)}
          />
        </div>
      </div>

      {selected.size > 0 ? (
        <p className="text-sm font-medium text-accent-ink" aria-live="polite">
          {selected.size} selected
        </p>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState
          icon={<UsersIcon className="size-6" />}
          title="No matching employees"
          description="No one matches this search or filter. Clear it to see the full directory."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Employee directory</caption>
            <thead>
              <tr className="border-b border-line text-xs font-semibold uppercase tracking-wide text-faint">
                <th scope="col" className="w-10 py-3 pl-1 pr-2 text-left">
                  <label className="inline-flex">
                    <span className="sr-only">Select all employees</span>
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      className="size-4 accent-accent"
                    />
                  </label>
                </th>
                <th scope="col" className="py-3 pr-3 text-left">Employee</th>
                <th scope="col" className="hidden py-3 pr-3 text-left md:table-cell">Designation</th>
                <th scope="col" className="hidden py-3 pr-3 text-left lg:table-cell">Department</th>
                <th scope="col" className="py-3 pr-3 text-left">Status</th>
                <th scope="col" className="hidden py-3 pr-3 text-left sm:table-cell">Location</th>
                <th scope="col" className="w-10 py-3 pr-1 text-right">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((emp) => {
                const isSelected = selected.has(emp.id);
                return (
                  <tr
                    key={emp.id}
                    className={cn(
                      "border-b border-line/70 last:border-0 transition-colors",
                      isSelected ? "bg-accent-soft/60" : "hover:bg-ground/60",
                    )}
                  >
                    <td className="py-3 pl-1 pr-2 align-middle">
                      <label className="inline-flex">
                        <span className="sr-only">Select {emp.full_name}</span>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleRow(emp.id)}
                          className="size-4 accent-accent"
                        />
                      </label>
                    </td>
                    <td className="py-3 pr-3 align-middle">
                      <div className="flex items-center gap-3">
                        <Avatar name={emp.full_name} size="sm" />
                        <div className="flex min-w-0 flex-col leading-tight">
                          <span className="truncate font-semibold text-ink">{emp.full_name}</span>
                          <span className="truncate text-xs text-muted">
                            {departmentLabel(emp)}
                          </span>
                        </div>
                      </div>
                    </td>
                    <td className="hidden py-3 pr-3 align-middle text-ink-soft md:table-cell">
                      {emp.designation ?? "—"}
                    </td>
                    <td className="hidden py-3 pr-3 align-middle text-muted lg:table-cell">
                      {departmentLabel(emp)}
                    </td>
                    <td className="py-3 pr-3 align-middle">
                      <EmployeeStatusPill status={emp.status} />
                    </td>
                    <td className="hidden py-3 pr-3 align-middle text-muted sm:table-cell">
                      <span className="inline-flex items-center gap-1.5">
                        <MapPinIcon className="size-4 text-faint" />
                        {emp.location_code ?? "—"}
                      </span>
                    </td>
                    <td className="py-3 pr-1 text-right align-middle">
                      <button
                        type="button"
                        aria-label={`More options for ${emp.full_name}`}
                        className="grid size-8 place-items-center rounded-full text-faint hover:bg-ground hover:text-ink transition-colors"
                      >
                        <MoreHorizontalIcon className="size-4" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
