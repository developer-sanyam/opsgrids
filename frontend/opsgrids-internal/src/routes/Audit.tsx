import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listAudit } from "@/api/client";
import type { AuditOut } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  Card,
  DataTable,
  EmptyState,
  EnvBadge,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ShieldIcon } from "@/ui/icons";

const NO_ROWS: AuditOut[] = [];

export function AuditPage() {
  const audit = useAsync(() => listAudit(), []);
  const [search, setSearch] = useState("");
  const [action, setAction] = useState<string>("all");

  const all = audit.status === "success" ? audit.data.audit : NO_ROWS;

  const actions = useMemo(() => [...new Set(all.map((a) => a.action))].sort(), [all]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return all.filter((a) => {
      const matchQuery =
        q === "" ||
        a.organization_id.toLowerCase().includes(q) ||
        (a.reason?.toLowerCase().includes(q) ?? false) ||
        a.entity_id?.toLowerCase().includes(q) === true;
      const matchAction = action === "all" || a.action === action;
      return matchQuery && matchAction;
    });
  }, [all, search, action]);

  const columns: Column<AuditOut>[] = [
    {
      key: "when",
      header: "When",
      render: (a) => <span className="tnum whitespace-nowrap text-muted">{formatDateTime(a.at)}</span>,
    },
    {
      key: "actor",
      header: "Actor",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (a) => (
        <div className="flex flex-col leading-tight">
          <span className="mono truncate text-xs text-ink">{a.actor_id ?? "system"}</span>
          <span className="text-xs text-faint">{a.acting_as ?? "—"}</span>
        </div>
      ),
    },
    {
      key: "action",
      header: "Action",
      mobile: "title",
      render: (a) => <span className="mono text-xs font-semibold text-accent-ink">{a.action}</span>,
    },
    {
      key: "entity",
      header: "Entity",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (a) => (
        <span className="mono text-xs text-muted">
          {a.entity}
          {a.entity_id ? `:${a.entity_id}` : ""}
        </span>
      ),
    },
    {
      key: "org",
      header: "Organization",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (a) => (
        <Link to={`/accounts/${a.organization_id}`} className="mono truncate text-sm font-semibold text-ink hover:text-accent transition-colors">
          {a.organization_id}
        </Link>
      ),
    },
    {
      key: "reason",
      header: "Reason",
      cellClassName: "max-w-md",
      render: (a) => <span className="text-muted">{a.reason ?? <span className="text-faint">—</span>}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Governance"
        title="Audit log"
        description="Every operator override and cross-tenant sensitive read, recorded by the owning service."
        actions={<EnvBadge />}
      />

      <p className="text-xs text-faint">
        Every row here already succeeded — the live audit trail only ever records a write after it
        completes, so there is no failed-attempt row to flag.
      </p>

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search the audit log"
            placeholder="Search by organization id, entity or reason…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div role="group" aria-label="Filter by action" className="-mx-1 -my-1 flex items-center gap-1.5 overflow-x-auto px-1 py-1">
            <Pill active={action === "all"} onClick={() => setAction("all")}>
              All actions
            </Pill>
            {actions.map((a) => (
              <Pill key={a} active={action === a} onClick={() => setAction(a)}>
                {a}
              </Pill>
            ))}
          </div>
        </div>
      </Card>

      <Card className="p-4 sm:p-5">
        {audit.status === "loading" ? (
          <div className="flex flex-col gap-2 py-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : audit.status === "error" ? (
          <ErrorState message={audit.error.message} />
        ) : (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(a) => a.id}
            caption="Operator audit log"
            emptyState={
              <EmptyState
                icon={<ShieldIcon className="size-6" />}
                title="No matching audit entries"
                description="Nothing matches these filters. Try a different action or clear the search."
              />
            }
          />
        )}
      </Card>
    </div>
  );
}
