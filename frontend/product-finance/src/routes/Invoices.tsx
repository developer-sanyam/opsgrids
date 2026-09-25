import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listInvoices } from "@/api/client";
import type { InvoiceSort, InvoiceStatusFilter } from "@/api/client";
import { getCustomerById } from "@/api/customers";
import type { Invoice } from "@/api/types";
import { customerName } from "@/data/customers";
import { outstandingMinor } from "@/data/derive";
import { downloadCsv } from "@/lib/csv";
import { formatDate } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  FilingStatusPill,
  InvoiceStatusPill,
  PageHeader,
  Pagination,
  Pill,
  SearchInput,
  Select,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column, TableSelection } from "@/ui";
import { DownloadIcon, PlusIcon, ReceiptIcon, useDebouncedValue } from "@opsgrids/ui";

const STATUS_FILTERS: { value: InvoiceStatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "sent", label: "Sent" },
  { value: "overdue", label: "Overdue" },
  { value: "paid", label: "Paid" },
  { value: "draft", label: "Draft" },
  { value: "void", label: "Void" },
];

const SORTS: { value: InvoiceSort; label: string }[] = [
  { value: "issued_desc", label: "Newest first" },
  { value: "issued_asc", label: "Oldest first" },
  { value: "amount_desc", label: "Amount: high to low" },
  { value: "amount_asc", label: "Amount: low to high" },
  { value: "due_asc", label: "Due date" },
];

function isStatusFilter(value: string | null): value is InvoiceStatusFilter {
  return (
    value === "all" ||
    value === "sent" ||
    value === "overdue" ||
    value === "paid" ||
    value === "draft" ||
    value === "void"
  );
}

/** The amount cell: negative credit-note totals read in the danger tone with a true minus sign. */
function AmountCell({ invoice }: { invoice: Invoice }) {
  const negative = invoice.total_minor < 0;
  return (
    <span className={negative ? "money font-semibold text-danger" : "money font-semibold text-ink"}>
      {negative ? "−" : ""}₹{formatAmount(Math.abs(invoice.total_minor))}
    </span>
  );
}

export function InvoicesPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState<InvoiceStatusFilter>(() => {
    const s = searchParams.get("status");
    return isStatusFilter(s) ? s : "all";
  });
  const [sort, setSort] = useState<InvoiceSort>("issued_desc");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  // Re-seed from the URL when arriving via a link (sidebar search, "view all" on the overview).
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
    const s = searchParams.get("status");
    if (isStatusFilter(s)) setStatus(s);
  }, [searchParams]);

  const loader = useCallback(
    () => listInvoices({ search: debouncedSearch, status, sort, page, size }),
    [debouncedSearch, status, sort, page, size],
  );
  const invoices = useAsync(loader, [debouncedSearch, status, sort, page, size]);
  // The directory resolves `customer_id` to a name; it never gates the table from rendering.
  const customers = useAsync(getCustomerById, []);
  const customerById = customers.status === "success" ? customers.data : undefined;

  const rows = useMemo(
    () => (invoices.status === "success" ? invoices.data.items : []),
    [invoices],
  );

  // A ticked row is a row on screen; a new page, sort or filter invalidates the selection rather
  // than carrying it to rows nobody saw ticked.
  useEffect(() => {
    setSelected(new Set());
  }, [debouncedSearch, status, sort, page, size]);

  const selection: TableSelection<Invoice> = {
    selected,
    onToggle: (key, next) =>
      setSelected((prev) => {
        const copy = new Set(prev);
        if (next) copy.add(key);
        else copy.delete(key);
        return copy;
      }),
    onToggleAll: (next) => setSelected(next ? new Set(rows.map((inv) => inv.id)) : new Set()),
    label: (inv) => inv.number,
  };

  function exportSelected() {
    const chosen = rows.filter((inv) => selected.has(inv.id));
    downloadCsv(`invoices-${new Date().toISOString().slice(0, 10)}.csv`, chosen, [
      { header: "Invoice", value: (inv) => inv.number },
      { header: "Customer", value: (inv) => customerName(customerById, inv.customer_id) },
      { header: "Issued", value: (inv) => formatDate(inv.issued_date) },
      { header: "Due", value: (inv) => formatDate(inv.due_date) },
      { header: "Amount", value: (inv) => formatAmount(Math.abs(inv.total_minor)) },
      { header: "Status", value: (inv) => inv.status },
    ]);
  }

  const columns: Column<Invoice>[] = [
    {
      key: "number",
      header: "Invoice",
      mobile: "title",
      render: (inv) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink tnum">{inv.number}</span>
          {inv.document_type === "credit_note" ? (
            <span className="mt-0.5">
              <Tag className="bg-accent-soft text-accent-ink">Credit note</Tag>
            </span>
          ) : null}
        </div>
      ),
    },
    {
      key: "customer",
      header: "Customer",
      render: (inv) => (
        <span className="text-ink-soft">{customerName(customerById, inv.customer_id)}</span>
      ),
    },
    {
      key: "issued",
      header: "Issued",
      cellClassName: "text-muted tnum whitespace-nowrap hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (inv) => formatDate(inv.issued_date),
    },
    {
      key: "due",
      header: "Due",
      cellClassName: "text-muted tnum whitespace-nowrap hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (inv) => formatDate(inv.due_date),
    },
    {
      key: "outstanding",
      header: "Outstanding",
      align: "right",
      cellClassName: "whitespace-nowrap hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (inv) => {
        const open = inv.status === "sent" || inv.status === "overdue";
        return open && inv.document_type === "invoice" ? (
          <span className="money text-ink-soft">₹{formatAmount(outstandingMinor(inv))}</span>
        ) : (
          <span className="text-faint">—</span>
        );
      },
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      cellClassName: "whitespace-nowrap",
      render: (inv) => <AmountCell invoice={inv} />,
    },
    {
      key: "filing",
      header: "E-invoice",
      align: "right",
      cellClassName: "whitespace-nowrap hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (inv) => <FilingStatusPill state={inv.einvoice} />,
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      mobile: "status",
      render: (inv) => <InvoiceStatusPill status={inv.status} />,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Accounts receivable"
        title="Invoices"
        actions={
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <ReceiptIcon className="size-4 text-muted" />
              <span className="tnum">{invoices.status === "success" ? invoices.data.total : "—"}</span>
              <span className="text-muted font-medium">documents</span>
            </span>
            <Can permission="finance.billing.invoice.issue" scope="ORG">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/invoices/new")}>
                New invoice
              </Button>
            </Can>
          </div>
        }
      />

      <p className="-mt-2 max-w-2xl text-sm text-muted">
        The invoice register is append-only. A mistake is corrected by issuing a{" "}
        <span className="font-semibold text-ink-soft">credit note</span> against the original —
        never by editing an issued invoice — so the audit trail stays intact.
      </p>

      {/* Controls */}
      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <SearchInput
            label="Search invoices"
            placeholder="Search by number or customer…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-xs"
          />
          <Select
            label="Sort invoices"
            containerClassName="lg:w-56"
            value={sort}
            onChange={(e) => setSort(e.target.value as InvoiceSort)}
            options={SORTS}
          />
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 border-t border-line pt-4">
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
      </Card>

      {/* Table */}
      <Card className="p-2 sm:p-4">
        {invoices.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {invoices.status === "error" ? (
          <ErrorState
            title="Couldn't load invoices"
            message={problemMessage(invoices.error)}
            onRetry={invoices.reload}
          />
        ) : null}
        {invoices.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(inv) => inv.id}
              onRowClick={(inv) => navigate(`/invoices/${inv.id}`)}
              caption="Invoices and credit notes"
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
                  icon={<ReceiptIcon className="size-6" />}
                  title={debouncedSearch || status !== "all" ? "No invoices match" : "No invoices yet"}
                  description={
                    debouncedSearch || status !== "all"
                      ? "Nothing matches these filters. Try a different status or clear the search."
                      : "Issue the first invoice to start billing."
                  }
                  action={
                    debouncedSearch || status !== "all" ? null : (
                      <Can permission="finance.billing.invoice.issue" scope="ORG">
                        <Button size="sm" onClick={() => navigate("/invoices/new")}>
                          New invoice
                        </Button>
                      </Can>
                    )
                  }
                />
              }
            />
            <Pagination
              page={invoices.data.page}
              pages={invoices.data.pages}
              total={invoices.data.total}
              size={invoices.data.size}
              noun="invoices"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>
    </div>
  );
}
