import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { deleteCompany, listCompanies } from "@/api/client";
import type { Company, CompanyStatus, Owner } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { companyImport } from "@/data/importExport/companies";
import type { CompanyFilters } from "@/data/importExport/companies";
import { ownerName } from "@/data/lookups";
import { downloadCsv } from "@/lib/csv";
import { formatMoneyCompact } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useImportExport, useMutation } from "@opsgrids/client";
import {
  Avatar,
  Button,
  Card,
  CompanyStatusPill,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  Pagination,
  PageHeader,
  Pill,
  RowActions,
  SearchInput,
  Select,
  Skeleton,
} from "@/ui";
import type { Column, TableSelection } from "@/ui";
import { BuildingIcon, DownloadIcon, GlobeIcon, ImportExportButton, MapPinIcon, PlusIcon, useDebouncedValue, useToast } from "@opsgrids/ui";
import { CompanyFormDialog } from "@/forms/CompanyFormDialog";

type StatusFilter = CompanyStatus | "all";
type Sort = "name" | "revenue" | "contacts";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "customer", label: "Customers" },
  { value: "prospect", label: "Prospects" },
  { value: "lead", label: "Leads" },
  { value: "churned", label: "Churned" },
];

const NO_COMPANIES: Company[] = [];

function buildColumns(
  owners: Owner[],
  canWrite: boolean,
  onEdit: (c: Company) => void,
  onDelete: (c: Company) => void,
): Column<Company>[] {
  return [
    {
      key: "name",
      header: "Company",
      mobile: "title",
      render: (c) => (
        <div className="flex items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
            <BuildingIcon className="size-4" />
          </span>
          <div className="flex min-w-0 flex-col leading-tight">
            <Link
              to={`/companies/${c.id}`}
              className="truncate font-semibold text-ink hover:text-accent transition-colors"
            >
              {c.name}
            </Link>
            <span className="inline-flex items-center gap-1 truncate text-xs text-muted">
              <GlobeIcon className="size-3" />
              {c.domain}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "industry",
      header: "Industry",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (c) => <span className="text-ink-soft">{c.industry}</span>,
    },
    {
      key: "location",
      header: "Location",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      mobile: "hidden",
      render: (c) => (
        <span className="inline-flex items-center gap-1.5 text-muted">
          <MapPinIcon className="size-4 text-faint" />
          {c.city}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (c) => <CompanyStatusPill status={c.status} />,
    },
    {
      key: "owner",
      header: "Owner",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (c) => (
        <span className="inline-flex items-center gap-2 min-w-0">
          <Avatar name={ownerName(owners, c.owner_id)} size="sm" />
          <span className="truncate text-sm text-ink-soft">{ownerName(owners, c.owner_id)}</span>
        </span>
      ),
    },
    {
      key: "revenue",
      header: "Annual revenue",
      align: "right",
      render: (c) => (
        <span className="tnum font-semibold text-ink">
          {formatMoneyCompact(c.annual_revenue_minor, c.currency)}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (c) => (
        <RowActions
          label={c.name}
          actions={[
            { label: "Edit", onSelect: () => onEdit(c), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => onDelete(c), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];
}

export function CompaniesPage() {
  const toast = useToast();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<Sort>("name");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Company | null>(null);
  const [deleting, setDeleting] = useState<Company | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const directory = useDirectory();
  const { owners, contacts } = directory;
  const canWrite = useCan("crm.book.company.write", "OWN");

  const loader = useCallback(
    () => listCompanies({ search: debouncedSearch, status, page, size }),
    [debouncedSearch, status, page, size],
  );
  const companies = useAsync(loader, [debouncedSearch, status, page, size]);

  // The export writes the whole filtered list, not the page on screen, so it needs the filters
  // the list itself is loaded with.
  const importFilters = useMemo<CompanyFilters>(
    () => ({ search: debouncedSearch, status }),
    [debouncedSearch, status],
  );
  const dx = useImportExport(companyImport, { filters: importFilters });

  // A ticked row is a row on screen (DataTable's contract for select-all); a new page or filter
  // invalidates it rather than carrying a stale selection across to rows nobody saw ticked.
  useEffect(() => {
    setSelected(new Set());
  }, [debouncedSearch, status, page, size]);

  const remove = useMutation((company: Company) => deleteCompany(company.id), {
    onSuccess: (_result, company) => {
      setDeleting(null);
      toast.success(`${company.name} deleted.`);
      companies.reload();
      directory.reload();
    },
  });

  const columns = useMemo(
    () => buildColumns(owners, canWrite, setEditing, setDeleting),
    [owners, canWrite],
  );

  const rows = useMemo(() => {
    const items = companies.status === "success" ? companies.data.items : NO_COMPANIES;
    const sorted = [...items];
    if (sort === "name") sorted.sort((a, b) => a.name.localeCompare(b.name));
    else if (sort === "revenue")
      sorted.sort((a, b) => b.annual_revenue_minor - a.annual_revenue_minor);
    else
      sorted.sort(
        (a, b) =>
          contacts.filter((c) => c.company_id === b.id).length -
          contacts.filter((c) => c.company_id === a.id).length,
      );
    return sorted;
  }, [companies, sort, contacts]);

  const selection: TableSelection<Company> = {
    selected,
    onToggle: (key, next) =>
      setSelected((prev) => {
        const copy = new Set(prev);
        if (next) copy.add(key);
        else copy.delete(key);
        return copy;
      }),
    onToggleAll: (next) => setSelected(next ? new Set(rows.map((c) => c.id)) : new Set()),
    label: (c) => c.name,
  };

  function exportSelected() {
    const chosen = rows.filter((c) => selected.has(c.id));
    downloadCsv(`companies-${new Date().toISOString().slice(0, 10)}.csv`, chosen, [
      { header: "Name", value: (c) => c.name },
      { header: "Domain", value: (c) => c.domain },
      { header: "Industry", value: (c) => c.industry },
      { header: "City", value: (c) => c.city },
      { header: "Status", value: (c) => c.status },
      { header: "Owner", value: (c) => ownerName(owners, c.owner_id) },
      { header: "Annual revenue", value: (c) => formatMoneyCompact(c.annual_revenue_minor, c.currency) },
    ]);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Records"
        title="Companies"
        actions={
          <>
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canWrite}
              canExport
            />
            <Can permission="crm.book.company.write" scope="OWN">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New company
              </Button>
            </Can>
          </>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search companies"
            placeholder="Search by name, industry or city…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
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
                  setPage(1);
                }}
              >
                {f.label}
              </Pill>
            ))}
          </div>
          <Select
            label="Sort companies"
            containerClassName="lg:ml-auto lg:w-52"
            value={sort}
            onChange={(e) => setSort(e.target.value as Sort)}
            options={[
              { value: "name", label: "Name A–Z" },
              { value: "revenue", label: "Revenue high–low" },
              { value: "contacts", label: "Most contacts" },
            ]}
          />
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {companies.status === "loading" ? (
          <div className="flex flex-col gap-3 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {companies.status === "error" ? (
          <ErrorState
            title="Couldn't load companies"
            message={problemMessage(companies.error)}
            onRetry={companies.reload}
          />
        ) : null}
        {companies.status === "success" ? (
          <>
            <DataTable
              caption="All companies"
              columns={columns}
              rows={rows}
              rowKey={(c) => c.id}
              mobileCard="auto"
              selection={selection}
              bulkActions={
                <Button
                  variant="outline"
                  size="sm"
                  leadingIcon={<DownloadIcon className="size-4" />}
                  onClick={exportSelected}
                >
                  Export selected
                </Button>
              }
              emptyState={
                <EmptyState
                  icon={<BuildingIcon className="size-6" />}
                  title={search ? "No companies match that search" : "No companies yet"}
                  description={
                    search
                      ? "Try a shorter search term, or clear the filters to see them all."
                      : "Create the first one to get started."
                  }
                  action={
                    search ? null : (
                      <Can permission="crm.book.company.write" scope="OWN">
                        <Button size="sm" onClick={() => setCreating(true)}>
                          New company
                        </Button>
                      </Can>
                    )
                  }
                />
              }
            />
            <Pagination
              page={companies.data.page}
              pages={companies.data.pages}
              total={companies.data.total}
              size={companies.data.size}
              noun="companies"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {creating ? (
        <CompanyFormDialog
          company={null}
          owners={owners}
          onOwnerCreated={() => directory.reload()}
          onClose={() => setCreating(false)}
          onSaved={(company) => {
            setCreating(false);
            toast.success(`${company.name} created.`);
            companies.reload();
            directory.reload();
          }}
        />
      ) : null}

      {editing ? (
        <CompanyFormDialog
          company={editing}
          owners={owners}
          onOwnerCreated={() => directory.reload()}
          onClose={() => setEditing(null)}
          onSaved={(company) => {
            setEditing(null);
            toast.success(`${company.name} saved.`);
            companies.reload();
            directory.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Delete this company?"
        body={`${deleting?.name} is removed from the account book. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
