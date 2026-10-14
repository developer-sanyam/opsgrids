import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { archiveCustomer, listCustomers } from "@/api/client";
import type { CustomerSort } from "@/api/client";
import type { CustomerStanding, CustomerSummary } from "@/api/types";
import { formatDate } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { cn, PlusIcon, useDebouncedValue, UsersIcon, useToast } from "@opsgrids/ui";
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
  RowActions,
  SearchInput,
  Select,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { CustomerFormDialog } from "@/forms/CustomerFormDialog";

const SORTS: { value: CustomerSort; label: string }[] = [
  { value: "receivables_desc", label: "Receivables: high to low" },
  { value: "overdue_desc", label: "Overdue: high to low" },
  { value: "name_asc", label: "Name: A–Z" },
];

const STANDING: Record<CustomerStanding, { label: string; tone: string }> = {
  overdue: { label: "Overdue", tone: "bg-danger-soft text-danger" },
  open: { label: "Open balance", tone: "bg-info-soft text-info" },
  clear: { label: "Clear", tone: "bg-present-soft text-present" },
};

function StandingPill({ standing }: { standing: CustomerStanding }) {
  const s = STANDING[standing];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold",
        s.tone,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {s.label}
    </span>
  );
}

export function CustomersPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [sort, setSort] = useState<CustomerSort>("receivables_desc");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CustomerSummary | null>(null);
  const [archiving, setArchiving] = useState<CustomerSummary | null>(null);
  const canWrite = useCan("finance.billing.customer.write", "ORG");

  const loader = useCallback(
    () => listCustomers({ search: debouncedSearch, sort, page, size }),
    [debouncedSearch, sort, page, size],
  );
  const customers = useAsync(loader, [debouncedSearch, sort, page, size]);

  const archive = useMutation((row: CustomerSummary) => archiveCustomer(row.customer.id), {
    onSuccess: (_result, row) => {
      setArchiving(null);
      toast.success(`${row.customer.name} archived.`);
      customers.reload();
    },
  });

  const rows = useMemo(
    () => (customers.status === "success" ? customers.data.items : []),
    [customers],
  );

  const columns: Column<CustomerSummary>[] = [
    {
      key: "name",
      header: "Customer",
      render: (row) => (
        <div className="flex items-center gap-3">
          <Avatar name={row.customer.name} size="md" />
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate font-bold text-ink">{row.customer.name}</span>
            <span className="truncate text-xs text-muted">
              {row.customer.city}, {row.customer.state}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "invoices",
      header: "Invoices",
      align: "right",
      cellClassName: "tnum text-muted hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (row) => row.invoice_count,
    },
    {
      key: "last",
      header: "Last invoice",
      cellClassName: "tnum text-muted whitespace-nowrap hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (row) => formatDate(row.last_invoice_date),
    },
    {
      key: "overdue",
      header: "Overdue",
      align: "right",
      cellClassName: "whitespace-nowrap hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (row) =>
        row.overdue_minor > 0 ? (
          <span className="money font-semibold text-danger">₹{formatAmount(row.overdue_minor)}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "receivables",
      header: "Receivables",
      align: "right",
      cellClassName: "whitespace-nowrap",
      render: (row) =>
        row.receivables_minor > 0 ? (
          <span className="money font-semibold text-ink">₹{formatAmount(row.receivables_minor)}</span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "standing",
      header: "Standing",
      align: "right",
      render: (row) => <StandingPill standing={row.standing} />,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (row) => (
        <RowActions
          label={row.customer.name}
          actions={[
            {
              label: "View invoices",
              onSelect: () => navigate(`/invoices?q=${encodeURIComponent(row.customer.name)}`),
            },
            { label: "Edit", onSelect: () => setEditing(row), hidden: !canWrite },
            {
              label: "Archive",
              tone: "danger",
              onSelect: () => setArchiving(row),
              hidden: !canWrite,
            },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Accounts receivable"
        title="Customers"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <UsersIcon className="size-4 text-muted" />
              <span className="tnum">{customers.status === "success" ? customers.data.total : "—"}</span>
              <span className="text-muted font-medium">accounts</span>
            </span>
            <Can permission="finance.billing.customer.write" scope="ORG">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                Add customer
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <SearchInput
          label="Search customers"
          placeholder="Search by name or city…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          containerClassName="sm:max-w-xs"
        />
        <Select
          label="Sort customers"
          containerClassName="sm:w-64"
          value={sort}
          onChange={(e) => setSort(e.target.value as CustomerSort)}
          options={SORTS}
        />
      </Card>

      <Card className="p-2 sm:p-4">
        {customers.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {customers.status === "error" ? (
          <ErrorState
            title="Couldn't load customers"
            message={problemMessage(customers.error)}
            onRetry={customers.reload}
          />
        ) : null}
        {customers.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.customer.id}
              caption="Customers and their receivable position"
              emptyState={
                <EmptyState
                  icon={<UsersIcon className="size-6" />}
                  title={debouncedSearch ? "No customers match that search" : "No customers yet"}
                  description={
                    debouncedSearch
                      ? "Try a different name or city, or clear the search."
                      : "Add the first customer to start issuing invoices."
                  }
                  action={
                    debouncedSearch ? (
                      <Button variant="outline" size="sm" onClick={() => setSearch("")}>
                        Clear search
                      </Button>
                    ) : (
                      <Can permission="finance.billing.customer.write" scope="ORG">
                        <Button size="sm" onClick={() => setCreating(true)}>
                          Add customer
                        </Button>
                      </Can>
                    )
                  }
                />
              }
            />
            <Pagination
              page={customers.data.page}
              pages={customers.data.pages}
              total={customers.data.total}
              size={customers.data.size}
              noun="customers"
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
        <CustomerFormDialog
          customer={null}
          onClose={() => setCreating(false)}
          onSaved={(customer) => {
            setCreating(false);
            toast.success(`${customer.name} created.`);
            customers.reload();
          }}
        />
      ) : null}

      {editing ? (
        <CustomerFormDialog
          customer={editing.customer}
          onClose={() => setEditing(null)}
          onSaved={(customer) => {
            setEditing(null);
            toast.success(`${customer.name} saved.`);
            customers.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={archiving !== null}
        onClose={() => {
          setArchiving(null);
          archive.reset();
        }}
        onConfirm={() => archiving && void archive.mutate(archiving)}
        title="Archive this customer?"
        body={`${archiving?.customer.name} is removed from the customer directory. This can't be undone.`}
        confirmLabel="Archive"
        pendingLabel="Archiving…"
        pending={archive.pending}
        {...(archive.error ? { error: problemMessage(archive.error) } : {})}
      />
    </div>
  );
}
