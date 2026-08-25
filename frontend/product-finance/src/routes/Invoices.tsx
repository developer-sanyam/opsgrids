import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listInvoices } from "@/api/client";
import type { InvoiceSort, InvoiceStatusFilter } from "@/api/client";
import { getCustomerById } from "@/api/customers";
import type { Invoice } from "@/api/types";
import { customerName } from "@/data/customers";
import { outstandingMinor } from "@/data/derive";
import { formatDate } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  InvoiceStatusPill,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { ReceiptIcon } from "@/ui/icons";

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
  const [status, setStatus] = useState<InvoiceStatusFilter>(() => {
    const s = searchParams.get("status");
    return isStatusFilter(s) ? s : "all";
  });
  const [sort, setSort] = useState<InvoiceSort>("issued_desc");

  // Re-seed from the URL when arriving via a link (sidebar search, "view all" on the overview).
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
    const s = searchParams.get("status");
    if (isStatusFilter(s)) setStatus(s);
  }, [searchParams]);

  const loader = useCallback(
    () => listInvoices({ search, status, sort }),
    [search, status, sort],
  );
  const invoices = useAsync(loader, [search, status, sort]);
  // The directory resolves `customer_id` to a name; it never gates the table from rendering.
  const customers = useAsync(getCustomerById, []);
  const customerById = customers.status === "success" ? customers.data : undefined;

  const rows = useMemo(
    () => (invoices.status === "success" ? invoices.data.items : []),
    [invoices],
  );

  const columns: Column<Invoice>[] = [
    {
      key: "number",
      header: "Invoice",
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
      key: "status",
      header: "Status",
      align: "right",
      render: (inv) => <InvoiceStatusPill status={inv.status} />,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Accounts receivable"
        title="Invoices"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <ReceiptIcon className="size-4 text-muted" />
            <span className="tnum">{invoices.status === "success" ? invoices.data.total : "—"}</span>
            <span className="text-muted font-medium">documents</span>
          </span>
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
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-xs"
          />
          <label className="inline-flex h-11 items-center gap-2 rounded-full bg-ground px-4 self-start lg:self-auto">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">Sort</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as InvoiceSort)}
              className="h-full cursor-pointer bg-transparent text-sm font-semibold text-ink focus:outline-none"
            >
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 border-t border-line pt-4">
          {STATUS_FILTERS.map((f) => (
            <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
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
        {invoices.status === "error" ? <ErrorState message={invoices.error.message} /> : null}
        {invoices.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(inv) => inv.id}
            onRowClick={(inv) => navigate(`/invoices/${inv.id}`)}
            caption="Invoices and credit notes"
            emptyState={
              <EmptyState
                icon={<ReceiptIcon className="size-6" />}
                title="No invoices match"
                description="Nothing matches these filters. Try a different status or clear the search."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
