import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { generateReturn, listReturns } from "@/api/client";
import type { ReturnSort, ReturnStatusFilter, ReturnTypeFilter } from "@/api/client";
import type { GstReturn } from "@/api/types";
import { formatMoney } from "@/lib/money";
import { ApiError, Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  FormDialog,
  PageHeader,
  Pagination,
  Pill,
  Select,
  Skeleton,
  Tag,
  TextField,
} from "@/ui";
import type { Column } from "@/ui";
import { PlusIcon, ShieldCheckIcon, useToast } from "@opsgrids/ui";

const STATUS_FILTERS: { value: ReturnStatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "draft", label: "Draft" },
  { value: "generated", label: "Generated" },
  { value: "filed", label: "Filed" },
];

const TYPE_FILTERS: { value: ReturnTypeFilter; label: string }[] = [
  { value: "all", label: "Every type" },
  { value: "gstr1", label: "GSTR-1" },
  { value: "gstr3b", label: "GSTR-3B" },
  { value: "gstr7", label: "GSTR-7" },
];

const SORTS: { value: ReturnSort; label: string }[] = [
  { value: "period_desc", label: "Newest period first" },
  { value: "period_asc", label: "Oldest period first" },
];

const STATUS_TONE: Record<string, string> = {
  draft: "bg-ground text-muted",
  generated: "bg-info-soft text-info",
  filed: "bg-present-soft text-present",
};

function GenerateReturnDialog({ onClose, onSaved }: { onClose: () => void; onSaved: (ret: GstReturn) => void }) {
  const [period, setPeriod] = useState("");
  const [returnType, setReturnType] = useState<"gstr1" | "gstr3b" | "gstr7">("gstr1");
  const [periodError, setPeriodError] = useState<string | undefined>(undefined);
  const periodRef = useRef<HTMLInputElement>(null);

  const generate = useMutation(
    (input: { period: string; return_type: "gstr1" | "gstr3b" | "gstr7" }) => generateReturn(input),
    { onSuccess: (ret) => onSaved(ret) },
  );

  function submit() {
    setPeriodError(undefined);
    if (!/^\d{4}-\d{2}$/.test(period)) {
      setPeriodError("Enter a period as YYYY-MM, like 2026-08.");
      periodRef.current?.focus();
      return;
    }
    void generate.mutate({ period, return_type: returnType });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Generate a GST return"
      description="Assembled from the invoices already recorded for this period. Generating an unfiled period again replaces the snapshot."
      submitLabel="Generate"
      pendingLabel="Generating…"
      pending={generate.pending}
      initialFocusRef={periodRef}
      {...(generate.error
        ? {
            error: problemMessage(generate.error),
            ...(generate.error instanceof ApiError && generate.error.problem?.request_id
              ? { reference: generate.error.problem.request_id }
              : {}),
          }
        : {})}
    >
      <TextField
        ref={periodRef}
        label="Period"
        type="month"
        placeholder="2026-08"
        value={period}
        onChange={(e) => setPeriod(e.target.value)}
        error={periodError}
      />
      <Select
        label="Return type"
        value={returnType}
        onChange={(e) => setReturnType(e.target.value as "gstr1" | "gstr3b" | "gstr7")}
        options={[
          { value: "gstr1", label: "GSTR-1" },
          { value: "gstr3b", label: "GSTR-3B" },
          { value: "gstr7", label: "GSTR-7" },
        ]}
      />
    </FormDialog>
  );
}

export function GstReturnsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [period, setPeriod] = useState("");
  const [status, setStatus] = useState<ReturnStatusFilter>("all");
  const [returnType, setReturnType] = useState<ReturnTypeFilter>("all");
  const [sort, setSort] = useState<ReturnSort>("period_desc");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [generating, setGenerating] = useState(false);

  const returns = useAsync(
    () => listReturns({ period: period.trim(), status, returnType, sort, page, size }),
    [period, status, returnType, sort, page, size],
  );
  const rows = useMemo(() => (returns.status === "success" ? returns.data.items : []), [returns]);

  const columns: Column<GstReturn>[] = [
    {
      key: "period",
      header: "Period",
      mobile: "title",
      render: (r) => <span className="font-semibold text-ink tnum">{r.period}</span>,
    },
    {
      key: "type",
      header: "Type",
      render: (r) => <Tag>{r.return_type.toUpperCase()}</Tag>,
    },
    {
      key: "taxable",
      header: "Taxable",
      align: "right",
      cellClassName: "whitespace-nowrap hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => <span className="money text-ink-soft">{formatMoney(r.total_taxable_minor)}</span>,
    },
    {
      key: "tax",
      header: "Tax",
      align: "right",
      cellClassName: "whitespace-nowrap hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (r) => (
        <span className="money text-ink-soft">
          {formatMoney(r.total_cgst_minor + r.total_sgst_minor + r.total_igst_minor)}
        </span>
      ),
    },
    {
      key: "documents",
      header: "Documents",
      align: "right",
      cellClassName: "tnum hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => r.document_count,
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      mobile: "status",
      render: (r) => (
        <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${STATUS_TONE[r.status] ?? "bg-ground text-muted"}`}>
          {r.status}
        </span>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="GST compliance"
        title="GST returns"
        actions={
          <Can permission="finance.compliance.returns.generate" scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setGenerating(true)}>
              Generate return
            </Button>
          </Can>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <TextField
            label="Period"
            type="month"
            placeholder="2026-08"
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value);
              setPage(1);
            }}
            optional
          />
          <Select
            label="Type"
            value={returnType}
            onChange={(e) => {
              setReturnType(e.target.value as ReturnTypeFilter);
              setPage(1);
            }}
            options={TYPE_FILTERS}
          />
          <Select
            label="Sort"
            value={sort}
            onChange={(e) => setSort(e.target.value as ReturnSort)}
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

      <Card className="p-2 sm:p-4">
        {returns.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {returns.status === "error" ? (
          <ErrorState
            title="Couldn't load GST returns"
            message={problemMessage(returns.error)}
            onRetry={returns.reload}
          />
        ) : null}
        {returns.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(r) => r.id}
              onRowClick={(r) => navigate(`/compliance/returns/${r.id}`)}
              caption="GST returns generated and filed"
              emptyState={
                <EmptyState
                  icon={<ShieldCheckIcon className="size-6" />}
                  title="No returns match"
                  description="Generate a period's return from the invoices already recorded, or clear the filters."
                />
              }
            />
            <Pagination
              page={returns.data.page}
              pages={returns.data.pages}
              total={returns.data.total}
              size={returns.data.size}
              noun="returns"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {generating ? (
        <GenerateReturnDialog
          onClose={() => setGenerating(false)}
          onSaved={(ret) => {
            setGenerating(false);
            toast.success(`${ret.period} ${ret.return_type.toUpperCase()} generated.`);
            navigate(`/compliance/returns/${ret.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
