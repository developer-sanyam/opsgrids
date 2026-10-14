import { useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getOverview, listInvoices, listPayments } from "@/api/client";
import { getCustomerById } from "@/api/customers";
import type { Customer, Invoice, InvoiceStatus, MonthlyRevenuePoint, Payment } from "@/api/types";
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
  ChartCard,
  ComplianceCard,
  DonutChart,
  EmptyState,
  ErrorState,
  KpiCard,
  LineChart,
  SectionHeader,
  Select,
  Skeleton,
  statusLabel,
  TABLET_QUERY,
  useMediaQuery,
} from "@/ui";
import type { ChartTone, DonutSlice, KpiTone } from "@/ui";
import {
  AlertTriangleIcon,
  CalendarIcon,
  PlusIcon,
  ReceiptIcon,
  TrendingUpIcon,
  WalletIcon,
} from "@opsgrids/ui";

/** Every invoice status, in the order the "Invoices by status" donut and its counts render. */
const INVOICE_STATUSES: InvoiceStatus[] = ["draft", "sent", "paid", "overdue", "void"];

const STATUS_TONE: Record<InvoiceStatus, ChartTone> = {
  paid: "positive",
  sent: "info",
  draft: "warning",
  overdue: "danger",
  void: "cyan",
};

type SeriesKey = "revenue" | "received" | "outstanding";

const SERIES_OPTIONS: { value: SeriesKey; label: string }[] = [
  { value: "revenue", label: "Revenue" },
  { value: "received", label: "Received" },
  { value: "outstanding", label: "Outstanding" },
];

function seriesValue(point: MonthlyRevenuePoint, key: SeriesKey): number {
  if (key === "revenue") return point.revenue_minor;
  if (key === "received") return point.received_minor;
  return point.outstanding_minor;
}

const SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Oct '25", "Nov", "Dec", "Jan '26" — short enough that twelve months of labels survive a
 *  three-a-side axis (`categoryStride`) on a 375px card without spilling out of it. The year marks
 *  only the first point and wherever it actually changes, the way a wall calendar would. */
function compactMonthLabels(points: readonly MonthlyRevenuePoint[]): string[] {
  return points.map((point, index) => {
    const [year, monthNumber] = point.month.split("-");
    const name = SHORT_MONTHS[Number(monthNumber ?? "1") - 1] ?? point.label;
    const previousYear = points[index - 1]?.month.split("-")[0];
    return index === 0 || year !== previousYear ? `${name} '${(year ?? "").slice(2)}` : name;
  });
}

/** The change against the prior month, captioned with the real month name — never invented.
 *  `null` (the prior month has nothing to compare against) falls back to a caption that states the
 *  period instead of a manufactured percentage. */
function trendCaption(deltaPct: number | null, previousLabel: string | undefined, fallback: string): string {
  return deltaPct !== null && previousLabel ? `vs ${previousLabel}` : fallback;
}

interface InvoiceStatusCounts {
  counts: Record<InvoiceStatus, number>;
  total: number;
}

async function loadInvoiceStatusCounts(): Promise<InvoiceStatusCounts> {
  const [draft, sent, paid, overdue, voidPage] = await Promise.all([
    listInvoices({ status: "draft", size: 1 }),
    listInvoices({ status: "sent", size: 1 }),
    listInvoices({ status: "paid", size: 1 }),
    listInvoices({ status: "overdue", size: 1 }),
    listInvoices({ status: "void", size: 1 }),
  ]);
  const counts: Record<InvoiceStatus, number> = {
    draft: draft.total,
    sent: sent.total,
    paid: paid.total,
    overdue: overdue.total,
    void: voidPage.total,
  };
  const total = INVOICE_STATUSES.reduce((sum, status) => sum + counts[status], 0);
  return { counts, total };
}

/** Per-KPI presentation: icon, pastel tone, and whether a rise reads as good news. Keyed so every
 *  lookup below is exact — never an optional fallback for a card that always renders. */
const KPI_STYLE: Record<"revenue" | "receivables" | "overdue" | "received", { icon: ReactNode; tone: KpiTone; goodWhen: "up" | "down" }> = {
  revenue: { icon: <TrendingUpIcon className="size-5" />, tone: "accent", goodWhen: "up" },
  receivables: { icon: <ReceiptIcon className="size-5" />, tone: "info", goodWhen: "up" },
  overdue: { icon: <AlertTriangleIcon className="size-5" />, tone: "danger", goodWhen: "down" },
  received: { icon: <WalletIcon className="size-5" />, tone: "positive", goodWhen: "up" },
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
        {period ? (
          <p className="max-w-2xl text-sm text-muted">
            Receivables, collections and compliance for {period}.
          </p>
        ) : null}
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

/** One payment in the "recent payments" list — there is no payment-detail screen to link to, so
 *  each row is informational rather than a navigation target. */
function PaymentRow({
  payment,
  customerById,
}: {
  payment: Payment;
  customerById: ReadonlyMap<string, Customer> | undefined;
}) {
  const name = customerName(customerById, payment.customer_id);
  return (
    <div className="flex items-center gap-3 rounded-tile px-2 py-3 -mx-2">
      <Avatar name={name} size="md" />
      <div className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate text-sm font-bold text-ink">{name}</span>
        <span className="truncate text-xs text-muted tnum">
          {payment.reference} · {formatDate(payment.received_date)}
        </span>
      </div>
      <span className="money shrink-0 text-sm font-bold text-positive">
        {formatMoney(payment.amount_minor)}
      </span>
    </div>
  );
}

function InvoicesByStatusCard({
  state,
  data,
  error,
  onRetry,
}: {
  state: "loading" | "success" | "error";
  data: InvoiceStatusCounts | null;
  error: Error | null;
  onRetry: () => void;
}) {
  const slices: DonutSlice[] = INVOICE_STATUSES.map((status) => ({
    key: status,
    label: statusLabel(status),
    value: data?.counts[status] ?? 0,
    tone: STATUS_TONE[status],
  }));

  return (
    <ChartCard
      title="Invoices by status"
      description="Every invoice you've issued, by where it stands"
      loading={state === "loading"}
      skeleton="donut"
      error={state === "error" ? <ErrorState message={problemMessage(error)} onRetry={onRetry} /> : undefined}
      empty={
        state === "success" && data && data.total === 0 ? (
          <EmptyState title="No invoices yet" description="Invoices you issue appear here, by status." />
        ) : undefined
      }
      action={
        state === "success" && data && data.total > 0 ? (
          <Link to="/invoices" className="relative hit-44 text-sm font-semibold text-accent hover:text-accent-strong transition-colors">
            View all
          </Link>
        ) : undefined
      }
    >
      {data && data.total > 0 ? (
        <DonutChart label="Invoices by status" slices={slices} centerValue={String(data.total)} size="sm" />
      ) : null}
    </ChartCard>
  );
}

export function OverviewPage() {
  const { user } = useAuth();
  const overview = useAsync(getOverview, []);
  // The directory resolves `customer_id` to a name; it never gates the screen from rendering.
  const customers = useAsync(getCustomerById, []);
  const customerById = customers.status === "success" ? customers.data : undefined;

  const invoiceStatus = useAsync(loadInvoiceStatusCounts, []);
  const recentPayments = useAsync(() => listPayments({ size: 5 }), []);

  const narrow = !useMediaQuery(TABLET_QUERY, true);
  const [seriesKey, setSeriesKey] = useState<SeriesKey>("revenue");

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const period = overview.status === "success" ? overview.data.period_label : "";

  const attention = useMemo(
    () => (overview.status === "success" ? overview.data.attention : []),
    [overview],
  );

  const history = overview.status === "success" ? (overview.data.revenue_history[0] ?? null) : null;
  const months = history?.months ?? [];
  const previousMonthLabel = months.length >= 2 ? months[months.length - 2]?.label : undefined;

  const overdueKpi =
    overview.status === "success" ? overview.data.kpis.find((k) => k.key === "overdue") : undefined;
  const receivablesKpi =
    overview.status === "success" ? overview.data.kpis.find((k) => k.key === "receivables") : undefined;
  const revenueKpi =
    overview.status === "success" ? overview.data.kpis.find((k) => k.key === "revenue") : undefined;

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
        {overview.status === "success" ? (
          <>
            <div style={{ "--i": 0 } as CSSProperties}>
              <KpiCard
                label="Revenue this period"
                value={formatMoneyCompact(history?.revenue_current_minor ?? revenueKpi?.value_minor ?? 0)}
                deltaPct={history?.revenue_delta_pct ?? undefined}
                caption={trendCaption(history?.revenue_delta_pct ?? null, previousMonthLabel, "This month")}
                figure="money"
                icon={KPI_STYLE.revenue.icon}
                tone={KPI_STYLE.revenue.tone}
                goodWhen={KPI_STYLE.revenue.goodWhen}
              />
            </div>
            <div style={{ "--i": 1 } as CSSProperties}>
              <KpiCard
                label="Outstanding receivables"
                value={formatMoneyCompact(history?.outstanding_current_minor ?? receivablesKpi?.value_minor ?? 0)}
                deltaPct={history?.outstanding_delta_pct ?? undefined}
                caption={trendCaption(history?.outstanding_delta_pct ?? null, previousMonthLabel, "Across all open invoices")}
                figure="money"
                icon={KPI_STYLE.receivables.icon}
                tone={KPI_STYLE.receivables.tone}
                goodWhen={KPI_STYLE.receivables.goodWhen}
              />
            </div>
            <div style={{ "--i": 2 } as CSSProperties}>
              <KpiCard
                label="Overdue"
                value={formatMoneyCompact(overdueKpi?.value_minor ?? 0)}
                caption={overdueKpi?.caption ?? "Past the due date"}
                figure="money"
                icon={KPI_STYLE.overdue.icon}
                tone={KPI_STYLE.overdue.tone}
                goodWhen={KPI_STYLE.overdue.goodWhen}
              />
            </div>
            <div style={{ "--i": 3 } as CSSProperties}>
              <KpiCard
                label="Received this period"
                value={formatMoneyCompact(history?.received_current_minor ?? 0)}
                deltaPct={history?.received_delta_pct ?? undefined}
                caption={trendCaption(history?.received_delta_pct ?? null, previousMonthLabel, "This month")}
                figure="money"
                icon={KPI_STYLE.received.icon}
                tone={KPI_STYLE.received.tone}
                goodWhen={KPI_STYLE.received.goodWhen}
              />
            </div>
          </>
        ) : null}
      </section>

      {/* Row 2 — revenue by month · receivables aging */}
      <section aria-label="Revenue and receivables" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
          <SectionHeader
            title="Revenue by month"
            subtitle="Real revenue, receipts and receivables — the last 12 months"
            action={
              overview.status === "success" && months.length > 0 ? (
                <Select
                  label="Series"
                  hideLabel
                  options={SERIES_OPTIONS}
                  value={seriesKey}
                  onChange={(e) => setSeriesKey(e.target.value as SeriesKey)}
                  containerClassName="w-36"
                />
              ) : undefined
            }
          />
          {overview.status === "success" ? (
            months.length > 0 ? (
              <LineChart
                label={`${SERIES_OPTIONS.find((o) => o.value === seriesKey)?.label ?? "Revenue"} by month`}
                categories={narrow ? compactMonthLabels(months) : months.map((p) => p.label)}
                categoryStride={narrow ? 3 : undefined}
                series={[
                  {
                    key: seriesKey,
                    label: SERIES_OPTIONS.find((o) => o.value === seriesKey)?.label ?? "Revenue",
                    values: months.map((p) => seriesValue(p, seriesKey)),
                  },
                ]}
                area
                smooth
                formatValue={formatMoneyCompact}
              />
            ) : (
              <EmptyState
                title="No revenue history yet"
                description="Revenue by month appears once invoices span two months."
              />
            )
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

      {/* Row 3 — invoices by status · recent payments */}
      <section aria-label="Invoices and payments" className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <InvoicesByStatusCard
          state={invoiceStatus.status}
          data={invoiceStatus.status === "success" ? invoiceStatus.data : null}
          error={invoiceStatus.status === "error" ? invoiceStatus.error : null}
          onRetry={invoiceStatus.reload}
        />

        <Card className="flex flex-col gap-3 p-6">
          <SectionHeader
            title="Recent payments"
            subtitle="Latest receipts, most recent first"
            action={
              recentPayments.status === "success" && recentPayments.data.total > 0 ? (
                <Link to="/payments" className="relative hit-44 text-sm font-semibold text-accent hover:text-accent-strong transition-colors">
                  View all
                </Link>
              ) : undefined
            }
          />
          {recentPayments.status === "loading" ? (
            <div className="flex flex-col gap-2 py-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {recentPayments.status === "error" ? (
            <ErrorState message={problemMessage(recentPayments.error)} onRetry={recentPayments.reload} />
          ) : null}
          {recentPayments.status === "success" ? (
            recentPayments.data.items.length === 0 ? (
              <EmptyState title="No payments yet" description="Payments you record appear here." />
            ) : (
              <div className="flex flex-col divide-y divide-line/70">
                {recentPayments.data.items.map((payment) => (
                  <PaymentRow key={payment.id} payment={payment} customerById={customerById} />
                ))}
              </div>
            )
          ) : null}
        </Card>
      </section>

      {/* Row 4 — GST compliance */}
      <section aria-label="GST compliance">
        <ComplianceCard customerById={customerById} />
      </section>

      {/* Row 5 — needs attention */}
      <section aria-label="Needs attention">
        <Card className="flex flex-col gap-3 p-6">
          <SectionHeader
            title="Needs attention"
            subtitle="Overdue invoices, largest first"
            action={
              <Link
                to="/invoices?status=overdue"
                className="relative hit-44 text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
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
