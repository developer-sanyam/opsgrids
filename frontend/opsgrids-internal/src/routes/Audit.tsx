import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { ShieldIcon } from "@opsgrids/ui";

import { listAudit } from "@/api/client";
import type { AuditEntry } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import {
  Card,
  DataTable,
  EmptyState,
  EnvBadge,
  LoadFailure,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
  StatusPill,
} from "@/ui";
import type { Column } from "@/ui";

function ResultBadge({ result }: { result: AuditEntry["result"] }) {
  const ok = result === "success";
  return <StatusPill tone={ok ? "active" : "suspended"}>{ok ? "Success" : "Failed"}</StatusPill>;
}

const NO_ROWS: AuditEntry[] = [];

export function AuditPage() {
  const audit = useAsync(() => listAudit(), []);
  const [search, setSearch] = useState("");
  const [action, setAction] = useState<string>("all");

  const all = audit.status === "success" ? audit.data.items : NO_ROWS;

  const actions = useMemo(() => [...new Set(all.map((a) => a.action))].sort(), [all]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return all.filter((a) => {
      const matchQuery =
        q === "" ||
        a.summary.toLowerCase().includes(q) ||
        a.actor.toLowerCase().includes(q) ||
        (a.organization_name?.toLowerCase().includes(q) ?? false);
      const matchAction = action === "all" || a.action === action;
      return matchQuery && matchAction;
    });
  }, [all, search, action]);

  const columns: Column<AuditEntry>[] = [
    {
      key: "when",
      header: "When",
      render: (a) => <span className="tnum whitespace-nowrap text-muted">{formatDateTime(a.occurred_at)}</span>,
    },
    {
      key: "actor",
      header: "Operator",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (a) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{a.actor}</span>
          <span className="money text-xs text-faint">{a.actor_email}</span>
        </div>
      ),
    },
    {
      key: "action",
      header: "Action",
      render: (a) => <span className="money text-xs font-semibold text-accent-ink">{a.action}</span>,
    },
    {
      key: "org",
      header: "Organization",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (a) =>
        a.organization_id ? (
          <Link
            to={`/accounts/${a.organization_id}`}
            className="font-semibold text-ink transition-colors hover:text-accent"
          >
            {a.organization_name}
          </Link>
        ) : (
          <span className="text-faint">platform</span>
        ),
    },
    {
      key: "summary",
      header: "Summary",
      cellClassName: "max-w-md",
      render: (a) => <span className="text-muted">{a.summary}</span>,
    },
    { key: "result", header: "Result", align: "right", render: (a) => <ResultBadge result={a.result} /> },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Governance"
        title="Audit log"
        description="Every operator override and cross-tenant sensitive read, recorded by the owning service."
        actions={<EnvBadge />}
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search the audit log"
            placeholder="Search by operator, organization or summary…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="-mx-1 flex items-center gap-1.5 overflow-x-auto px-1" role="group" aria-label="Action">
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
          <div className="flex flex-col gap-2 py-2" aria-busy>
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : audit.status === "error" ? (
          <LoadFailure error={audit.error} title="Couldn't load the audit log" onRetry={audit.reload} />
        ) : (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(a) => a.audit_id}
            caption="Operator audit log"
            dense
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
