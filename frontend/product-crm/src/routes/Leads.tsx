import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listLeads } from "@/api/client";
import type { Lead, LeadStatus } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { leadImport } from "@/data/importExport/leads";
import type { LeadFilters } from "@/data/importExport/leads";
import { formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useImportExport } from "@opsgrids/client";
import { ImportExportButton, PlusIcon, UserPlusIcon } from "@opsgrids/ui";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  LeadStatusPill,
  OwnerBadge,
  PageHeader,
  Pagination,
  Pill,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ChipRow } from "@/ui/ChipRow";
import { LeadFormDialog } from "@/forms/LeadFormDialog";

type StatusFilter = LeadStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "new", label: "New" },
  { value: "working", label: "Working" },
  { value: "qualified", label: "Qualified" },
  { value: "disqualified", label: "Disqualified" },
  { value: "converted", label: "Converted" },
];

const PAGE_SIZE = 25;

function buildColumns(): Column<Lead>[] {
  return [
    {
      key: "name",
      header: "Lead",
      mobile: "title",
      render: (l) => (
        <div className="flex flex-col leading-tight">
          <Link
            to={`/leads/${l.id}`}
            className="relative hit-44 font-semibold text-ink hover:text-accent transition-colors"
          >
            {l.full_name}
          </Link>
          <span className="text-xs text-muted">{l.company ?? "—"}</span>
        </div>
      ),
    },
    { key: "status", header: "Status", mobile: "status", render: (l) => <LeadStatusPill status={l.status} /> },
    {
      key: "source",
      header: "Source",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (l) => <span className="capitalize text-ink-soft">{l.source}</span>,
    },
    {
      key: "owner",
      header: "Owner",
      mobile: "hidden",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (l) => (l.owner_id ? <OwnerBadge ownerId={l.owner_id} /> : <span className="text-faint">Unassigned</span>),
    },
    {
      key: "created",
      header: "Captured",
      mobile: "hidden",
      align: "right",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (l) => <span className="tnum text-ink-soft">{formatDate(l.created_at)}</span>,
    },
  ];
}

export function LeadsPage() {
  const directory = useDirectory();
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(PAGE_SIZE);
  const [creating, setCreating] = useState(false);

  const loader = useCallback(
    () => listLeads({ status, page, size }),
    [status, page, size],
  );
  const leads = useAsync(loader, [status, page, size]);
  const columns = useMemo(() => buildColumns(), []);
  const canCreate = useCan("crm.lead.lead.write", "OWN");

  // The export writes the whole filtered list, not the page on screen, so it needs the filters
  // the list itself is loaded with.
  const importFilters = useMemo<LeadFilters>(() => ({ status }), [status]);
  // New rows show without a refresh: the list reloads.
  const { reload: reloadLeads } = leads;
  const dx = useImportExport(leadImport, { filters: importFilters, onImported: reloadLeads });

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Leads"
        description="Captured ahead of the pipeline — qualify a lead, then convert it into a company and a deal."
        actions={
          <>
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canCreate}
              canExport
            />
            <Can permission="crm.lead.lead.write" scope="OWN">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                Capture lead
              </Button>
            </Can>
          </>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <ChipRow label="Filter leads by status">
          {STATUS_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={status === f.value}
              onClick={() => {
                setStatus(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
        </ChipRow>
      </Card>

      {leads.status === "loading" ? (
        <Card className="p-4">
          <div className="flex flex-col gap-3 p-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        </Card>
      ) : null}

      {leads.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load leads"
            message={problemMessage(leads.error)}
            onRetry={leads.reload}
          />
        </Card>
      ) : null}

      {leads.status === "success" ? (
        leads.data.items.length === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={<UserPlusIcon className="size-6" />}
              title="No leads yet"
              description="Capture the first one by hand, or wait for the web form and imports to start flowing in."
              action={
                <Can permission="crm.lead.lead.write" scope="OWN">
                  <Button size="sm" onClick={() => setCreating(true)}>
                    Capture lead
                  </Button>
                </Can>
              }
            />
          </Card>
        ) : (
          <>
            <Card className="p-2 sm:p-4">
              <DataTable
                caption="Leads in the intake"
                columns={columns}
                rows={leads.data.items}
                rowKey={(l) => l.id}
              />
            </Card>
            <Pagination
              page={leads.data.page}
              pages={leads.data.pages}
              total={leads.data.total}
              size={leads.data.size}
              noun="leads"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        )
      ) : null}

      {creating ? (
        <LeadFormDialog
          owners={directory.owners}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            leads.reload();
          }}
        />
      ) : null}
    </div>
  );
}
