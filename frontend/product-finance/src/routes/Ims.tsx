import { useCallback, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listImsDocuments } from "@/api/client";
import type { ImsReasonFilter, ImsSort, ImsStatusFilter } from "@/api/client";
import type { ImsDocument } from "@/api/types";
import { isOutstanding, reasonLabel, suggestionSentence } from "@/data/compliance";
import { readErrorMessage } from "@/lib/errors";
import { formatDate } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { useAsync } from "@opsgrids/client";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  ImsStatusPill,
  PageHeader,
  Pill,
  SampleDataChip,
  SearchInput,
  Skeleton,
  SuggestedAction,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { InboxIcon } from "@opsgrids/ui";

const STATUS_FILTERS: { value: ImsStatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Undecided" },
  { value: "pending_action", label: "On hold" },
  { value: "accepted", label: "Accepted" },
  { value: "rejected", label: "Rejected" },
];

const REASON_FILTERS: { value: ImsReasonFilter; label: string }[] = [
  { value: "all", label: "Every reason" },
  { value: "matched", label: reasonLabel("matched") },
  { value: "amount_mismatch", label: reasonLabel("amount_mismatch") },
  { value: "no_purchase_record", label: reasonLabel("no_purchase_record") },
  { value: "supplier_unknown", label: reasonLabel("supplier_unknown") },
  { value: "duplicate", label: reasonLabel("duplicate") },
];

const SORTS: { value: ImsSort; label: string }[] = [
  { value: "date_asc", label: "Oldest first" },
  { value: "date_desc", label: "Newest first" },
  { value: "amount_desc", label: "Amount: high to low" },
  { value: "amount_asc", label: "Amount: low to high" },
];

const DOCUMENT_TYPE_LABEL: Record<ImsDocument["document_type"], string> = {
  invoice: "Invoice",
  credit_note: "Credit note",
  debit_note: "Debit note",
};

function isStatusFilter(value: string | null): value is ImsStatusFilter {
  return STATUS_FILTERS.some((f) => f.value === value);
}

/**
 * The suggestion cell — the column this queue is actually worked by.
 *
 * The reason comes first and in plain words. "Amount differs" with "₹1,180.00 more than your
 * purchase record" underneath is a row somebody can decide in one glance; `amount_mismatch` with
 * a signed integer of paise is a row they have to go and investigate.
 */
function SuggestionCell({ document }: { document: ImsDocument }) {
  const suggestion = document.suggestion ?? null;
  if (suggestion === null) {
    return (
      <span className="text-sm text-muted">
        Not reconciled yet — nothing is suggested for this one.
      </span>
    );
  }
  return (
    <div className="flex flex-col items-start gap-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <SuggestedAction action={suggestion.suggested_action} />
        <Tag>{reasonLabel(suggestion.reason)}</Tag>
      </div>
      <span className="max-w-sm text-sm text-ink-soft">{suggestionSentence(suggestion)}</span>
    </div>
  );
}

export function ImsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<ImsStatusFilter>(() => {
    const initial = searchParams.get("status");
    return isStatusFilter(initial) ? initial : "all";
  });
  const [reason, setReason] = useState<ImsReasonFilter>("all");
  const [sort, setSort] = useState<ImsSort>("date_asc");

  const loader = useCallback(
    () => listImsDocuments({ search, status, reason, sort }),
    [search, status, reason, sort],
  );
  const queue = useAsync(loader, [search, status, reason, sort]);

  const rows = useMemo(() => (queue.status === "success" ? queue.data.items : []), [queue]);
  const outstanding = rows.filter((row) => isOutstanding(row.status)).length;

  const columns: Column<ImsDocument>[] = [
    {
      key: "supplier",
      header: "Supplier",
      render: (doc) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{doc.supplier_name}</span>
          {/* The GSTIN is fifteen monospaced characters and sets the column's minimum width on its
              own. Below `sm` it goes, so the decision column stays on screen rather than behind a
              sideways scroll — the detail view carries it in full. */}
          <span className="money hidden text-xs text-muted sm:inline">{doc.supplier_gstin}</span>
        </div>
      ),
    },
    {
      key: "document",
      header: "Document",
      render: (doc) => (
        <div className="flex flex-col leading-tight">
          <span className="money text-sm font-semibold text-ink-soft">{doc.document_number}</span>
          <span className="text-xs text-muted tnum">
            {formatDate(doc.document_date)}
            {doc.document_type !== "invoice" ? ` · ${DOCUMENT_TYPE_LABEL[doc.document_type]}` : ""}
          </span>
        </div>
      ),
    },
    {
      key: "amount",
      header: "They declare",
      align: "right",
      cellClassName: "whitespace-nowrap hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (doc) => (
        <span className="money font-semibold text-ink">₹{formatAmount(doc.total_minor)}</span>
      ),
    },
    {
      key: "suggestion",
      header: "What we see",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (doc) => <SuggestionCell document={doc} />,
    },
    {
      key: "status",
      header: "Decision",
      align: "right",
      render: (doc) => <ImsStatusPill status={doc.status} />,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="GST compliance"
        title="Supplier reconciliation"
        actions={
          <span className="flex items-center gap-2">
            <SampleDataChip />
            <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
              <InboxIcon className="size-4 text-muted" />
              <span className="tnum">{queue.status === "success" ? outstanding : "—"}</span>
              <span className="font-medium text-muted">awaiting you</span>
            </span>
          </span>
        }
      />

      <p className="-mt-2 max-w-2xl text-sm text-muted">
        Documents your suppliers filed against this organization. Accepting one{" "}
        <span className="font-semibold text-ink-soft">claims the input tax credit</span> on it;
        rejecting declines the credit and sends the document back. The platform reconciles each
        against your purchase book and says what it found — the decision is yours, and every one
        is recorded with your name against it.
      </p>

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <SearchInput
            label="Search supplier documents"
            placeholder="Search by supplier, GSTIN or document number…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <label className="inline-flex h-11 items-center gap-2 self-start rounded-full bg-ground px-4 lg:self-auto">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">Sort</span>
            <select
              value={sort}
              onChange={(event) => setSort(event.target.value as ImsSort)}
              className="h-full cursor-pointer bg-transparent text-sm font-semibold text-ink focus:outline-none"
            >
              {SORTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-col gap-3 border-t border-line pt-4">
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            <span className="mr-1 shrink-0 text-xs font-semibold uppercase tracking-wide text-faint">
              Decision
            </span>
            {STATUS_FILTERS.map((filter) => (
              <Pill
                key={filter.value}
                active={status === filter.value}
                onClick={() => setStatus(filter.value)}
              >
                {filter.label}
              </Pill>
            ))}
          </div>
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            <span className="mr-1 shrink-0 text-xs font-semibold uppercase tracking-wide text-faint">
              Reason
            </span>
            {REASON_FILTERS.map((filter) => (
              <Pill
                key={filter.value}
                active={reason === filter.value}
                onClick={() => setReason(filter.value)}
              >
                {filter.label}
              </Pill>
            ))}
          </div>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {queue.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {queue.status === "error" ? (
          <ErrorState message={readErrorMessage(queue.error, "the reconciliation queue")} />
        ) : null}
        {queue.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(doc) => doc.id}
            onRowClick={(doc) => navigate(`/compliance/ims/${doc.id}`)}
            caption="Inbound supplier documents awaiting reconciliation"
            emptyState={
              <EmptyState
                icon={<InboxIcon className="size-6" />}
                title="Nothing in this view"
                description="No supplier document matches these filters. Clear the search, or widen the decision and reason filters."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
