import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { getImsPushSummary, listImsDocuments } from "@/api/client";
import type { ImsPushFilter, ImsReasonFilter, ImsSort, ImsStatusFilter } from "@/api/client";
import type { ImsAction, ImsDocument, ImsPushSummary, Page } from "@/api/types";
import {
  PUSH_STATUS_FILTERS,
  actionLabel,
  periodLabel,
  reasonLabel,
  suggestionSentence,
} from "@/data/compliance";
import { readErrorMessage } from "@/lib/errors";
import { formatDate } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { useQuietRefresh } from "@/lib/useQuietRefresh";
import { useAsync, useAuth } from "@opsgrids/client";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  ImsBulkBar,
  ImsPushPanel,
  ImsStatusPill,
  PageHeader,
  Pagination,
  Pill,
  PushBadge,
  SampleDataChip,
  SearchInput,
  Select,
  Skeleton,
  SuggestedAction,
  Tag,
  TextField,
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

/** How often the queue and the period's counts re-read while a push is under way, so the answer
 *  from GSTN shows up without a reload. Not while everything is at rest. */
const ACTIVE_REFRESH_MS = 6_000;

const PERIOD_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

/** Some decision of the period is asked for or in the portal's hands, and no answer is back yet. */
function pushUnderWay(summary: ImsPushSummary | null): boolean {
  return summary !== null && (summary.requested > 0 || summary.submitting > 0);
}

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
  const { user } = useAuth();

  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState<ImsStatusFilter>(() => {
    const initial = searchParams.get("status");
    return isStatusFilter(initial) ? initial : "all";
  });
  const [reason, setReason] = useState<ImsReasonFilter>("all");
  const [sort, setSort] = useState<ImsSort>("date_asc");
  const [period, setPeriod] = useState("");
  const [pushFilter, setPushFilter] = useState<ImsPushFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  // What is ticked, and what was typed beside each row. Both belong to the page on screen: a
  // change of filter or page clears them, because an action that could reach rows the person can
  // no longer see is how a bulk decision goes wrong.
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set());
  const [reasons, setReasons] = useState<Readonly<Record<string, string>>>({});
  const [receipt, setReceipt] = useState<string | null>(null);

  const validPeriod = PERIOD_PATTERN.test(period) ? period : "";

  const loader = useCallback(
    () =>
      listImsDocuments({
        search: debouncedSearch,
        status,
        reason,
        sort,
        period: validPeriod,
        pushStatus: pushFilter,
        page,
        size,
      }),
    [debouncedSearch, status, reason, sort, validPeriod, pushFilter, page, size],
  );
  const queue = useAsync(loader, [debouncedSearch, status, reason, sort, validPeriod, pushFilter, page, size]);
  const loadedQueue = queue.status === "success" ? queue.data : null;

  const summaryLoader = useCallback(
    () => (validPeriod === "" ? Promise.resolve(null) : getImsPushSummary(validPeriod)),
    [validPeriod],
  );
  const summaryState = useAsync<ImsPushSummary | null>(summaryLoader, [validPeriod]);
  const loadedSummary = summaryState.status === "success" ? summaryState.data : null;

  // A push under way is worth watching: while any decision of the period is asked for and not yet
  // answered, both the counts and the rows re-read quietly (no skeletons, so nothing on screen —
  // a dialog, a half-typed reason — is torn down by the poll). The counts decide when to stop:
  // asked of what is shown, so the poll ends when an answer shows nothing left under way.
  const { shown: summary, refresh: refreshSummary } = useQuietRefresh<ImsPushSummary | null>(
    loadedSummary,
    summaryLoader,
    { active: (current) => pushUnderWay(current), intervalMs: ACTIVE_REFRESH_MS },
  );
  const watching = pushUnderWay(summary);
  const { shown: shownQueue, refresh: refreshQueue } = useQuietRefresh<Page<ImsDocument>>(
    loadedQueue,
    loader,
    { active: () => watching, intervalMs: ACTIVE_REFRESH_MS },
  );

  useEffect(() => {
    setSelectedIds(new Set());
    setReasons({});
  }, [debouncedSearch, status, reason, sort, validPeriod, pushFilter, page, size]);

  const rows = useMemo(() => shownQueue?.items ?? [], [shownQueue]);
  const selectedDocs = useMemo(() => rows.filter((doc) => selectedIds.has(doc.id)), [rows, selectedIds]);

  const afterChange = useCallback(() => {
    refreshQueue();
    refreshSummary();
  }, [refreshQueue, refreshSummary]);

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
      cellClassName: "hidden xl:table-cell",
      headerClassName: "hidden xl:table-cell",
      render: (doc) => <SuggestionCell document={doc} />,
    },
    {
      key: "reason",
      header: "Your reason",
      cellClassName: "min-w-[12rem]",
      render: (doc) => (
        // A text field inside a row that is itself a control: without stopping the event, typing
        // a space would open the document, and clicking the field would navigate away from it.
        <div
          role="presentation"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          <TextField
            label={`Reason for ${doc.document_number}`}
            hideLabel
            maxLength={1000}
            placeholder="Why — kept with your decision"
            value={reasons[doc.id] ?? ""}
            onChange={(event) => setReasons((prev) => ({ ...prev, [doc.id]: event.target.value }))}
            optional
          />
        </div>
      ),
    },
    {
      key: "status",
      header: "Decision",
      align: "right",
      render: (doc) => (
        <div className="flex flex-col items-end gap-1.5">
          <ImsStatusPill status={doc.status} />
          <PushBadge push={doc.push} />
        </div>
      ),
    },
  ];

  function onRecorded({ count, action }: { count: number; action: ImsAction }) {
    setSelectedIds(new Set());
    setReasons({});
    setReceipt(
      `Recorded ${count} decision${count === 1 ? "" : "s"} — ${actionLabel(action).toLowerCase()}. Each has your name and its own reason, and is queued to be sent to GSTN; nothing is sent until someone with the push permission sends the period.`,
    );
    afterChange();
  }

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
              <span className="tnum">{shownQueue !== null ? shownQueue.total : "—"}</span>
              <span className="font-medium text-muted">documents</span>
            </span>
          </span>
        }
      />

      <p className="-mt-2 max-w-2xl text-sm text-muted">
        Documents your suppliers filed against this organization. Accepting one{" "}
        <span className="font-semibold text-ink-soft">claims the input tax credit</span> on it;
        rejecting declines the credit and sends the document back. The platform reconciles each
        against your purchase book and says what it found — the decision is yours, and every one
        is recorded with your name and your reason against it, whether decided alone or ticked
        together.
      </p>

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <SearchInput
            label="Search supplier documents"
            placeholder="Search by supplier, GSTIN or document number…"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <TextField
              label="Return period"
              type="month"
              value={period}
              onChange={(event) => {
                setPeriod(event.target.value);
                setPage(1);
              }}
              helper="Sending is made one period at a time."
              containerClassName="sm:w-48"
              optional
            />
            <Select
              label="Sort supplier documents"
              containerClassName="sm:w-56"
              value={sort}
              onChange={(event) => setSort(event.target.value as ImsSort)}
              options={SORTS}
            />
          </div>
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
                onClick={() => {
                  setStatus(filter.value);
                  setPage(1);
                }}
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
                onClick={() => {
                  setReason(filter.value);
                  setPage(1);
                }}
              >
                {filter.label}
              </Pill>
            ))}
          </div>
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            <span className="mr-1 shrink-0 text-xs font-semibold uppercase tracking-wide text-faint">
              On GSTN
            </span>
            {PUSH_STATUS_FILTERS.map((filter) => (
              <Pill
                key={filter.value}
                active={pushFilter === filter.value}
                onClick={() => {
                  setPushFilter(filter.value);
                  setPage(1);
                }}
              >
                {filter.label}
              </Pill>
            ))}
          </div>
        </div>
      </Card>

      <ImsPushPanel
        period={validPeriod}
        summary={validPeriod === "" ? null : summary}
        onRequested={(requested) => {
          setReceipt(
            requested === 0
              ? `Nothing in ${periodLabel(validPeriod)} was waiting to be sent.`
              : `Asked for ${requested} decision${requested === 1 ? "" : "s"} for ${periodLabel(validPeriod)} to be sent to GSTN. Each row shows where it stands as GSTN answers.`,
          );
          afterChange();
        }}
      />

      {receipt !== null ? (
        <p role="status" className="rounded-tile bg-present-soft px-4 py-3 text-sm font-medium text-present">
          {receipt}
        </p>
      ) : null}

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
        {shownQueue !== null ? (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(doc) => doc.id}
              onRowClick={(doc) => navigate(`/compliance/ims/${doc.id}`)}
              caption="Inbound supplier documents awaiting reconciliation"
              selection={{
                selected: selectedIds,
                onToggle: (key, next) =>
                  setSelectedIds((prev) => {
                    const copy = new Set(prev);
                    if (next) copy.add(key);
                    else copy.delete(key);
                    return copy;
                  }),
                onToggleAll: (next) => setSelectedIds(next ? new Set(rows.map((doc) => doc.id)) : new Set()),
                label: (doc) => `${doc.supplier_name} ${doc.document_number}`,
              }}
              toolbar={
                selectedDocs.length > 0 ? (
                  <ImsBulkBar
                    selected={selectedDocs}
                    reasons={reasons}
                    decidedBy={user?.full_name ?? "You"}
                    onClear={() => setSelectedIds(new Set())}
                    onRecorded={onRecorded}
                  />
                ) : null
              }
              emptyState={
                <EmptyState
                  icon={<InboxIcon className="size-6" />}
                  title="Nothing in this view"
                  description="No supplier document matches these filters. Clear the search, or widen the decision, reason and GSTN filters."
                />
              }
            />
            <Pagination
              page={shownQueue.page}
              pages={shownQueue.pages}
              total={shownQueue.total}
              size={shownQueue.size}
              noun="documents"
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
