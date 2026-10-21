import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listLeads } from "@/api/client";
import type { LeadSelection } from "@/api/coldEmail";
import type { Lead, LeadSource, LeadStatus } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { useMyRep } from "@/data/useMyRep";
import { leadImport } from "@/data/importExport/leads";
import type { LeadFilters } from "@/data/importExport/leads";
import { formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useEntitlement, useImportExport } from "@opsgrids/client";
import { ImportExportButton, MailIcon, PlusIcon, UserPlusIcon, ChipRow, useDebouncedValue, useToast } from "@opsgrids/ui";
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
  SearchInput,
  Select,
  Skeleton,
} from "@/ui";
import type { Column, TableSelection } from "@/ui";
import { EmailLeadsDialog } from "@/forms/EmailLeadsDialog";
import { LeadFormDialog } from "@/forms/LeadFormDialog";

type StatusFilter = LeadStatus | "all";
type SourceFilter = LeadSource | "all";

const SOURCE_FILTERS: { value: SourceFilter; label: string }[] = [
  { value: "all", label: "All sources" },
  { value: "form", label: "Web form" },
  { value: "import", label: "Import" },
  { value: "manual", label: "Manual" },
];

/** What the email dialog is for, in the Leads list's own words. */
function describe(status: StatusFilter, source: SourceFilter): string {
  if (status === "all" && source === "all") return "all the leads you can see";
  return `${status === "all" ? "all" : status} leads${source === "all" ? "" : ` from ${source}`}`;
}

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
  const myRep = useMyRep();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 250);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(PAGE_SIZE);
  const [source, setSource] = useState<SourceFilter>("all");
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [emailing, setEmailing] = useState<{ selection: LeadSelection; label: string } | null>(null);
  const toast = useToast();

  const loader = useCallback(
    () =>
      listLeads({
        ...(debouncedSearch.trim() ? { q: debouncedSearch.trim() } : {}),
        status,
        source,
        page,
        size,
      }),
    [debouncedSearch, status, source, page, size],
  );
  const leads = useAsync(loader, [debouncedSearch, status, source, page, size]);
  const columns = useMemo(() => buildColumns(), []);
  const canCreate = useCan("crm.lead.lead.write", "OWN");
  // Hides the control only; the server checks the entitlement, both permissions and lead scope.
  const entitledToEngage = useEntitlement("engage");
  const canDraft = useCan("engage.campaign.campaign.write", "ORG");
  const canSend = useCan("engage.campaign.campaign.send", "ORG");
  const canEmail = entitledToEngage && canDraft && canSend;

  function emailMatching() {
    setEmailing({
      selection: { kind: "filter", status: status === "all" ? null : status, source: source === "all" ? null : source },
      label: describe(status, source),
    });
  }
  function emailSelected() {
    const n = selected.size;
    setEmailing({
      selection: { kind: "ids", ids: [...selected] },
      label: `${n} selected ${n === 1 ? "lead" : "leads"}`,
    });
  }
  const selection: TableSelection<Lead> = {
    selected,
    onToggle: (key, next) =>
      setSelected((prev) => {
        const copy = new Set(prev);
        if (next) copy.add(key);
        else copy.delete(key);
        return copy;
      }),
    onToggleAll: (next) =>
      setSelected(next && leads.status === "success" ? new Set(leads.data.items.map((l) => l.id)) : new Set()),
    label: (l) => l.full_name,
  };

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
            {canEmail ? (
              <Button variant="outline" leadingIcon={<MailIcon className="size-4" />} onClick={emailMatching}>
                Email these leads
              </Button>
            ) : null}
            <Can permission="crm.lead.lead.write" scope="OWN">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                Capture lead
              </Button>
            </Can>
          </>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search leads"
            placeholder="Search by name, company, email or phone…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
              setSelected(new Set());
            }}
            containerClassName="w-full lg:max-w-xs"
          />
          <ChipRow label="Filter leads by status" className="overflow-x-auto">
            {STATUS_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={status === f.value}
                onClick={() => {
                  setStatus(f.value);
                  setPage(1);
                  setSelected(new Set());
                }}
              >
                {f.label}
              </Pill>
            ))}
          </ChipRow>
          <Select
            label="Source"
            containerClassName="w-full sm:max-w-xs lg:w-44"
            value={source}
            onChange={(e) => {
              setSource(e.target.value as SourceFilter);
              setPage(1);
              setSelected(new Set());
            }}
            options={SOURCE_FILTERS}
          />
        </div>
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
              title={search ? "No leads match that search" : "No leads yet"}
              description={
                search
                  ? "Try a different name, company or clear the search to see all leads."
                  : "Capture the first one by hand, or wait for the web form and imports to start flowing in."
              }
              action={
                search ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setSearch("");
                      setPage(1);
                      setSelected(new Set());
                    }}
                  >
                    Clear search
                  </Button>
                ) : (
                  <Can permission="crm.lead.lead.write" scope="OWN">
                    <Button size="sm" onClick={() => setCreating(true)}>
                      Capture lead
                    </Button>
                  </Can>
                )
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
                {...(canEmail
                  ? {
                      selection,
                      bulkActions: (
                        <Button variant="outline" size="sm" leadingIcon={<MailIcon className="size-4" />} onClick={emailSelected}>
                          Email selected
                        </Button>
                      ),
                    }
                  : {})}
              />
            </Card>
            <Pagination
              page={leads.data.page}
              pages={leads.data.pages}
              total={leads.data.total}
              size={leads.data.size}
              noun="leads"
              onPageChange={(n) => {
                setPage(n);
                setSelected(new Set());
              }}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
                setSelected(new Set());
              }}
            />
          </>
        )
      ) : null}

      {emailing ? (
        <EmailLeadsDialog
          selection={emailing.selection}
          selectionLabel={emailing.label}
          onClose={() => setEmailing(null)}
          onDone={(message) => {
            setEmailing(null);
            setSelected(new Set());
            toast.success(message);
          }}
        />
      ) : null}

      {creating ? (
        <LeadFormDialog
          owners={directory.owners}
          ensureMyRep={myRep.ensure}
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
