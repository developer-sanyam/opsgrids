import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { deleteSupplier, listSuppliers } from "@/api/client";
import type { Category, SupplierOut, SupplierStatus } from "@/api/types";
import { CATEGORY_LABEL } from "@/data/derive";
import { supplierImport } from "@/data/importExport/suppliers";
import type { SupplierFilters } from "@/data/importExport/suppliers";
import {
  Can,
  problemMessage,
  useAsync,
  useCan,
  useImportExport,
  useMutation,
} from "@opsgrids/client";
import { downloadCsv } from "@/lib/csv";
import { formatMoney } from "@/lib/format";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  SearchInput,
  Select,
  Skeleton,
  SupplierStatusPill,
} from "@/ui";
import type { Column, TableSelection } from "@/ui";
import {
  DownloadIcon,
  FactoryIcon,
  ImportExportButton,
  PlusIcon,
  useDebouncedValue,
  useToast,
} from "@opsgrids/ui";
import { SupplierFormDialog } from "@/forms/SupplierFormDialog";

const CATEGORY_FILTERS: { value: Category | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "raw_materials", label: "Raw materials" },
  { value: "components", label: "Components" },
  { value: "packaging", label: "Packaging" },
  { value: "mro", label: "MRO" },
  { value: "logistics", label: "Logistics" },
  { value: "services", label: "Services" },
];

const STATUS_FILTERS: { value: SupplierStatus | "all"; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "on_hold", label: "On hold" },
  { value: "pending", label: "Pending" },
];

type SortKey = "name" | "lead_time" | "on_time" | "spend";

/** Colour the on-time figure by band so a weak supplier is legible — with the number carrying it. */
function onTimeClass(pct: number): string {
  if (pct >= 95) return "text-present";
  if (pct >= 85) return "text-ink";
  return "text-late";
}

export function SuppliersPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [category, setCategory] = useState<Category | "all">("all");
  const [status, setStatus] = useState<SupplierStatus | "all">("all");
  const [sort, setSort] = useState<SortKey>("spend");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<SupplierOut | null>(null);
  const [deleting, setDeleting] = useState<SupplierOut | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const canWrite = useCan("operations.procurement.supplier.write", "ORG");

  const sortParam = sort === "lead_time" ? "lead_time" : sort === "on_time" ? "on_time" : sort;
  const loader = () =>
    listSuppliers({ q: debouncedSearch, category, status, sort: sortParam, page, size });
  const state = useAsync(loader, [debouncedSearch, category, status, sort, page, size]);
  const rows = state.status === "success" ? state.data.items : [];

  // The export is the whole list under these filters, not the page on screen.
  const filters = useMemo<SupplierFilters>(
    () => ({ q: debouncedSearch, category, status, sort: sortParam }),
    [debouncedSearch, category, status, sortParam],
  );
  // `state.reload` keeps one identity for the life of the screen. The dialog stays open on its
  // result, so the rows it just created appear behind it as soon as the run ends.
  const dx = useImportExport(supplierImport, { filters, onImported: state.reload });

  // A ticked row is a row on screen; a new page, sort or filter invalidates the selection rather
  // than carrying it to rows nobody saw ticked.
  useEffect(() => {
    setSelected(new Set());
  }, [debouncedSearch, category, status, sort, page, size]);

  const selection: TableSelection<SupplierOut> = {
    selected,
    onToggle: (key, next) =>
      setSelected((prev) => {
        const copy = new Set(prev);
        if (next) copy.add(key);
        else copy.delete(key);
        return copy;
      }),
    onToggleAll: (next) => setSelected(next ? new Set(rows.map((s) => s.id)) : new Set()),
    label: (s) => s.name,
  };

  function exportSelected() {
    const chosen = rows.filter((s) => selected.has(s.id));
    downloadCsv(`suppliers-${new Date().toISOString().slice(0, 10)}.csv`, chosen, [
      { header: "Supplier", value: (s) => s.name },
      { header: "Code", value: (s) => s.code },
      { header: "Category", value: (s) => CATEGORY_LABEL[s.category] },
      { header: "Lead time (days)", value: (s) => String(s.lead_time_days) },
      { header: "On-time %", value: (s) => s.on_time_pct.toFixed(1) },
      { header: "Spend", value: (s) => formatMoney(s.spend_minor) },
      { header: "Status", value: (s) => s.status },
    ]);
  }

  const remove = useMutation((supplier: SupplierOut) => deleteSupplier(supplier.id), {
    onSuccess: (_result, supplier) => {
      setDeleting(null);
      toast.success(`${supplier.name} deleted.`);
      state.reload();
    },
  });

  const columns: Column<SupplierOut>[] = [
    {
      key: "name",
      header: "Supplier",
      sortKey: "name",
      mobile: "title",
      render: (s) => (
        <button
          type="button"
          onClick={() => navigate(`/suppliers/${s.id}`)}
          className="flex items-center gap-3 text-left"
        >
          <Avatar name={s.name} size="sm" />
          <span className="flex flex-col leading-tight">
            <span className="font-semibold text-ink hover:text-accent transition-colors">
              {s.name}
            </span>
            <span className="text-xs text-faint tnum">{s.code}</span>
          </span>
        </button>
      ),
    },
    {
      key: "category",
      header: "Category",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (s) => <span className="text-ink-soft">{CATEGORY_LABEL[s.category]}</span>,
    },
    {
      key: "lead",
      header: "Lead time",
      align: "right",
      sortKey: "lead_time",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (s) => (
        <span className="tnum text-muted whitespace-nowrap">{s.lead_time_days} days</span>
      ),
    },
    {
      key: "on_time",
      header: "On-time",
      align: "right",
      sortKey: "on_time",
      render: (s) => (
        <span className={`tnum font-semibold ${onTimeClass(s.on_time_pct)}`}>
          {s.on_time_pct.toFixed(1)}%
        </span>
      ),
    },
    {
      key: "spend",
      header: "Spend",
      align: "right",
      sortKey: "spend",
      render: (s) => <span className="tnum font-semibold text-ink">{formatMoney(s.spend_minor)}</span>,
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      mobile: "status",
      render: (s) => (
        <div className="flex justify-end">
          <SupplierStatusPill status={s.status} />
        </div>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (s) => (
        <RowActions
          label={s.name}
          actions={[
            { label: "View", onSelect: () => navigate(`/suppliers/${s.id}`) },
            { label: "Edit", onSelect: () => setEditing(s), hidden: !canWrite },
            {
              label: "Delete",
              tone: "danger",
              onSelect: () => setDeleting(s),
              hidden: !canWrite,
              disabled: s.open_pos > 0,
              disabledReason: "This supplier has an open purchase order.",
            },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Procurement"
        title="Suppliers"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canWrite}
              canExport
            />
            <Can permission="operations.procurement.supplier.write" scope="ORG">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New supplier
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search suppliers"
            placeholder="Search by name, code or location…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <Select
            label="Status"
            containerClassName="lg:w-44"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as SupplierStatus | "all");
              setPage(1);
            }}
            options={STATUS_FILTERS}
          />
          <Select
            label="Sort suppliers"
            containerClassName="lg:ml-auto lg:w-52"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            options={[
              { value: "spend", label: "Spend high–low" },
              { value: "on_time", label: "On-time high–low" },
              { value: "lead_time", label: "Lead time low–high" },
              { value: "name", label: "Name A–Z" },
            ]}
          />
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 border-t border-line pt-4">
          <span className="pr-1 text-xs font-semibold uppercase tracking-wide text-faint shrink-0">
            Category
          </span>
          {CATEGORY_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={category === f.value}
              onClick={() => {
                setCategory(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState
            title="Couldn't load suppliers"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(s) => s.id}
              caption="Suppliers"
              onRowClick={(s) => navigate(`/suppliers/${s.id}`)}
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
                  icon={<FactoryIcon className="size-6" />}
                  title={debouncedSearch || category !== "all" || status !== "all" ? "No suppliers match these filters" : "No suppliers yet"}
                  description={
                    debouncedSearch || category !== "all" || status !== "all"
                      ? "Try a different category or status, or clear the search."
                      : "Add the first one to start raising purchase orders."
                  }
                  action={
                    debouncedSearch || category !== "all" || status !== "all" ? null : (
                      <Can permission="operations.procurement.supplier.write" scope="ORG">
                        <Button size="sm" onClick={() => setCreating(true)}>
                          New supplier
                        </Button>
                      </Can>
                    )
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="suppliers"
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
        <SupplierFormDialog
          supplier={null}
          onClose={() => setCreating(false)}
          onSaved={(supplier) => {
            setCreating(false);
            toast.success(`${supplier.name} created.`);
            state.reload();
          }}
        />
      ) : null}

      {editing ? (
        <SupplierFormDialog
          supplier={editing}
          onClose={() => setEditing(null)}
          onSaved={(supplier) => {
            setEditing(null);
            toast.success(`${supplier.name} saved.`);
            state.reload();
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
        title="Delete this supplier?"
        body={`${deleting?.name} is removed from the supplier directory. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
