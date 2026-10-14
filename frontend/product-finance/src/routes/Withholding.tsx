import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listWithholdingRates, listWithholdings } from "@/api/client";
import type { WithholdingCode, WithholdingWithInvoice } from "@/api/types";
import { formatDate } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { problemMessage, useAsync } from "@opsgrids/client";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Select,
  SectionHeader,
  Skeleton,
  Tag,
  TextField,
} from "@/ui";
import type { Column } from "@/ui";
import { ScaleIcon } from "@opsgrids/ui";

const CODE_OPTIONS: { value: WithholdingCode | "all"; label: string }[] = [
  { value: "all", label: "Every head" },
  { value: "gst_tds_51", label: "GST TDS — s.51" },
  { value: "itax_194q", label: "Income tax TDS — s.194Q" },
  { value: "itax_206c_1h", label: "Income tax TCS — s.206C(1H)" },
];

const CODE_LABEL: Record<string, string> = {
  gst_tds_51: "GST TDS · s.51",
  itax_194q: "Income tax TDS · s.194Q",
  itax_206c_1h: "Income tax TCS · s.206C(1H)",
};

export function WithholdingPage() {
  const [period, setPeriod] = useState("");
  const [code, setCode] = useState<WithholdingCode | "all">("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  const register = useAsync(
    () => listWithholdings({ period: period.trim(), code, page, size }),
    [period, code, page, size],
  );
  const rates = useAsync(listWithholdingRates, []);
  const rows = useMemo(() => (register.status === "success" ? register.data.items : []), [register]);

  const columns: Column<WithholdingWithInvoice>[] = [
    {
      key: "invoice",
      header: "Invoice",
      mobile: "title",
      render: (row) => (
        <Link
          to={`/invoices/${row.withholding.invoice_id}`}
          className="relative hit-44 font-semibold text-ink hover:text-accent transition-colors tnum"
        >
          {row.invoice_number}
        </Link>
      ),
    },
    {
      key: "counterparty",
      header: "Counterparty",
      render: (row) => (
        <div className="flex flex-col leading-tight">
          <span className="text-ink-soft">{row.withholding.counterparty_name || "—"}</span>
          <span className="money text-xs text-muted">{row.withholding.counterparty_gstin ?? "—"}</span>
        </div>
      ),
    },
    {
      key: "code",
      header: "Head",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (row) => <Tag>{CODE_LABEL[row.withholding.code] ?? row.withholding.code}</Tag>,
    },
    {
      key: "period",
      header: "Period",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (row) => row.withholding.period,
    },
    {
      key: "basis",
      header: "Basis",
      align: "right",
      cellClassName: "whitespace-nowrap hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (row) => <span className="money text-ink-soft">{formatMoney(row.withholding.basis_minor)}</span>,
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      mobile: "status",
      cellClassName: "whitespace-nowrap",
      render: (row) => <span className="money font-semibold text-ink">{formatMoney(row.withholding.amount_minor)}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="GST compliance" title="TDS / TCS withholding" />
      <p className="-mt-2 max-w-2xl text-sm text-muted">
        What was deducted from what's owed to this organization, and what this organization
        collected on top. Read-only — recorded at invoice finalization.
      </p>

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <TextField
            label="Period"
            placeholder="2026-08"
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value);
              setPage(1);
            }}
            optional
          />
          <Select
            label="Withholding head"
            value={code}
            onChange={(e) => {
              setCode(e.target.value as WithholdingCode | "all");
              setPage(1);
            }}
            options={CODE_OPTIONS}
          />
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {register.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {register.status === "error" ? (
          <ErrorState
            title="Couldn't load the withholding register"
            message={problemMessage(register.error)}
            onRetry={register.reload}
          />
        ) : null}
        {register.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.withholding.id}
              caption="TDS deducted and TCS collected, per document"
              emptyState={
                <EmptyState
                  icon={<ScaleIcon className="size-6" />}
                  title="Nothing withheld yet"
                  description="Nothing matches this filter, or no invoice has triggered a withholding entry."
                />
              }
            />
            <Pagination
              page={register.data.page}
              pages={register.data.pages}
              total={register.data.total}
              size={register.data.size}
              noun="entries"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      <Card className="flex flex-col gap-4 p-6">
        <SectionHeader title="Rate schedule" subtitle="The statutory withholding schedule — global vocabulary, every version" />
        {rates.status === "loading" ? <Skeleton className="h-32 w-full rounded-tile" /> : null}
        {rates.status === "error" ? (
          <ErrorState message={problemMessage(rates.error)} onRetry={rates.reload} />
        ) : null}
        {rates.status === "success" ? (
          rates.data.length === 0 ? (
            <p className="text-sm text-muted">No withholding rates published.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <caption className="sr-only">Withholding rate schedule</caption>
                <thead>
                  <tr className="border-b border-line text-xs font-semibold uppercase tracking-wide text-faint">
                    <th scope="col" className="py-2.5 pr-3 text-left">Head</th>
                    <th scope="col" className="py-2.5 px-3 text-right">Rate</th>
                    <th scope="col" className="py-2.5 px-3 text-right">Threshold</th>
                    <th scope="col" className="py-2.5 px-3 text-left">Basis</th>
                    <th scope="col" className="py-2.5 pl-3 text-left">Effective</th>
                  </tr>
                </thead>
                <tbody>
                  {rates.data.map((rate) => (
                    <tr key={rate.id} className="border-b border-line/70 last:border-0">
                      <td className="py-3 pr-3 text-ink-soft">{rate.name}</td>
                      <td className="py-3 px-3 text-right tnum text-ink">{rate.rate_pct}%</td>
                      <td className="py-3 px-3 text-right money text-ink-soft">{formatMoney(rate.threshold_minor)}</td>
                      <td className="py-3 px-3 text-ink-soft capitalize">{rate.basis}</td>
                      <td className="py-3 pl-3 tnum text-muted">
                        {formatDate(rate.effective_from)}
                        {rate.effective_to ? ` – ${formatDate(rate.effective_to)}` : " – ongoing"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </Card>
    </div>
  );
}
