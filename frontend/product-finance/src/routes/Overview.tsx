import { useMemo } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getOverview } from "@/api/client";
import { getCustomerById } from "@/api/customers";
import type { Customer, Invoice, OverviewKpi } from "@/api/types";
import { Can, problemMessage, useAsync, useAuth } from "@opsgrids/client";
import { customerName } from "@/data/customers";
import { daysOverdue, outstandingMinor } from "@/data/derive";
import { formatDate, today } from "@/lib/format";
import { formatMoney, formatMoneyCompact } from "@/lib/money";
import {
  AgingBreakdown,
  Avatar,
  Button,
  Card,
  CashflowChart,
  ComplianceCard,
  ErrorState,
  KpiCard,
  SampleChip,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";
import {
  AlertTriangleIcon,
  CalendarIcon,
  PlusIcon,
  ReceiptIcon,
  TrendingUpIcon,
  WalletIcon,
} from "@opsgrids/ui";

/** Per-KPI presentation: icon, pastel tone, and whether a rise reads as bad news. */
const KPI_STYLE: Record<string, { icon: ReactNode; tone: KpiTone; invert: boolean }> = {
  revenue: { icon: <TrendingUpIcon className="size-5" />, tone: "accent", invert: false },
  receivables: { icon: <ReceiptIcon className="size-5" />, tone: "info", invert: false },
  overdue: { icon: <AlertTriangleIcon className="size-5" />, tone: "danger", invert: true },
  cash: { icon: <WalletIcon className="size-5" />, tone: "cyan", invert: false },
};

function OverviewHeader({ orgName, period }: { orgName: string; period: string }) {
  const navigate = useNavigate();
  return (
    <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
      <div className="flex flex-col gap-1">
        <nav aria-label="Breadcrumb" className="text-sm font-medium text-muted">
          Workspace <span className="text-faint">/</span>{" "}
          <span className="text-ink-soft">{orgName}</span>
        </nav>
        <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">
          Overview
        </h1>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">{period}</span>
        </span>
        <Can permission="finance.billing.invoice.issue" scope="ORG">
          <Button
            size="md"
            leadingIcon={<PlusIcon className="size-4" />}
            onClick={() => navigate("/invoices/new")}
          >
            New invoice
          </Button>
        </Can>
      </div>
    </header>
  );
}

function KpiValue({ kpi }: { kpi: OverviewKpi }) {
  const style = KPI_STYLE[kpi.key] ?? { icon: <ReceiptIcon className="size-5" />, tone: "accent" as KpiTone, invert: false };
  return (
    <KpiCard
      label={kpi.label}
      value={formatMoneyCompact(kpi.value_minor)}
      deltaPct={kpi.deltaPct}
      series={kpi.series}
      icon={style.icon}
      tone={style.tone}
      caption={kpi.caption}
      captionPlacement="label"
      figure="money"
      goodWhen={style.invert ? "down" : "up"}
      sample={kpi.sample ? true : kpi.trend_sample ? { label: "Sample trend" } : false}
    />
  );
}

/** One overdue invoice in the "needs attention" queue. */
function AttentionRow({
  invoice,
  customerById,
}: {
  invoice: Invoice;
  customerById: ReadonlyMap<string, Customer> | undefined;
}) {
  const name = customerName(customerById, invoice.customer_id);
  const overdueDays = daysOverdue(invoice, today());
  return (
    <Link
      to={`/invoices/${invoice.id}`}
      className="flex items-center gap-3 rounded-tile px-2 py-2.5 -mx-2 hover:bg-ground transition-colors focus-visible:bg-ground"
    >
      <Avatar name={name} size="md" />
      <div className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate text-sm font-bold text-ink">{name}</span>
        <span className="truncate text-xs text-muted tnum">
          {invoice.number} · due {formatDate(invoice.due_date)}
        </span>
      </div>
      <div className="flex flex-col items-end leading-tight">
        <span className="money text-sm font-bold text-ink">
          {formatMoney(outstandingMinor(invoice))}
        </span>
        <span className="text-xs font-semibold text-danger tnum">
          {overdueDays} {overdueDays === 1 ? "day" : "days"} overdue
        </span>
      </div>
    </Link>
  );
}

export function OverviewPage() {
  const { user } = useAuth();
  const overview = useAsync(getOverview, []);
  // The directory resolves `customer_id` to a name; it never gates the screen from rendering.
  const customers = useAsync(getCustomerById, []);
  const customerById = customers.status === "success" ? customers.data : undefined;

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const period = overview.status === "success" ? overview.data.period_label : "";

  const attention = useMemo(
    () => (overview.status === "success" ? overview.data.attention : []),
    [overview],
  );

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} period={period} />

      {/* Row 1 — KPI cards */}
      <section
        aria-label="Key metrics"
        className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        {overview.status === "loading"
          ? Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="flex flex-col gap-4 p-5" style={{ "--i": i } as CSSProperties}>
                <div className="flex justify-between">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-8 w-20" />
                </div>
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-9 w-24 rounded-tile" />
              </Card>
            ))
          : null}
        {overview.status === "error" ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
          </Card>
        ) : null}
        {overview.status === "success"
          ? overview.data.kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiValue kpi={kpi} />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — cashflow · aging */}
      <section aria-label="Cash and receivables" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
          <SectionHeader
            title="Cashflow"
            subtitle="Money in and out, last six months"
            action={overview.status === "success" && overview.data.cashflow_sample ? <SampleChip /> : undefined}
          />
          {overview.status === "success" ? (
            <CashflowChart data={overview.data.cashflow} />
          ) : overview.status === "error" ? (
            <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Receivables aging" subtitle="Open invoices by age" />
          {overview.status === "success" ? (
            <AgingBreakdown buckets={overview.data.aging} />
          ) : overview.status === "error" ? (
            <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — GST compliance */}
      <section aria-label="GST compliance">
        <ComplianceCard customerById={customerById} />
      </section>

      {/* Row 4 — needs attention */}
      <section aria-label="Needs attention">
        <Card className="flex flex-col gap-3 p-6">
          <SectionHeader
            title="Needs attention"
            subtitle="Overdue invoices, largest first"
            action={
              <Link
                to="/invoices?status=overdue"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                View all
              </Link>
            }
          />
          {overview.status === "loading" ? (
            <div className="flex flex-col gap-2 py-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {overview.status === "success" ? (
            attention.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                Nothing overdue. Every open invoice is within its terms.
              </p>
            ) : (
              <div className="flex flex-col divide-y divide-line/70">
                {attention.map((invoice) => (
                  <AttentionRow
                    key={invoice.id}
                    invoice={invoice}
                    customerById={customerById}
                  />
                ))}
              </div>
            )
          ) : null}
        </Card>
      </section>
    </div>
  );
}
