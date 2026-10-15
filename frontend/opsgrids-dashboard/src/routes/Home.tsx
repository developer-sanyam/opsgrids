/**
 * The Owner/Executive home (`/`; the product Marketplace moved to `/products`).
 *
 * Every figure here is read straight from the product that owns it — no dashboard-only
 * aggregate, no client-side rollup of a scanned list. Every tenant token shares audience `app`
 * (`services/app-gateway/app/config.py`), so this one session may call any product's own summary
 * endpoint through the gateway; whether it *should* is decided per widget, twice: is the product
 * entitled (the token's `ent` claim) and does the caller hold the grant the endpoint itself
 * requires (`GET /members/me/grants`, already loaded by `PermissionProvider`). A widget that
 * fails either check never fetches — see `src/ui/home/access.ts`.
 *
 * "Orders" does not exist as a business object anywhere in the platform (no sales-order entity):
 * the mockup's Total Orders / Orders by Status / Recent Orders are relabelled here to what is
 * real — finance invoices — rather than filled with an invented number. Top Products and Team
 * Performance are left out entirely: no product links an invoice line to a catalogue item, and no
 * service computes a performance figure for a person. Inventing either would be exactly the
 * "sample" data the dashboard audit spent itself removing.
 */
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { getPlatformHost, problemMessage, scopeFor, useAsync, useAuth, useCan, usePermissions } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  Button,
  CalendarIcon,
  Card,
  ChartCard,
  ClipboardCheckIcon,
  DataTable,
  DonutChart,
  EmptyState,
  ErrorState,
  formatRelative,
  HeroMetricCard,
  isStatusWord,
  LineChart,
  MetricCard,
  PageHeader,
  ReceiptIcon,
  SectionHeader,
  Select,
  Skeleton,
  SquareIcon,
  statusLabel,
  StatusBadge,
  Timeline,
  TrendingUpIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { Column, DonutSlice } from "@opsgrids/ui";

import {
  countCrmCustomers,
  getFinanceOverview,
  listDealActivities,
  listFinanceCustomers,
  listFinanceInvoices,
  listMyTasks,
} from "@/api";
import type {
  Company,
  DealActivity,
  FinanceCustomerSummary,
  FinanceInvoiceSummary,
  FinanceOverview,
  MonthlyRevenuePoint,
  Page,
  ProjectTask,
} from "@/api/types";
import { formatDate, formatMoneyKpi, formatMoneyWhole } from "@/lib/money";
import { resolveAccess } from "@/ui/home/access";
import type { AccessState } from "@/ui/home/access";
import { EmployeeWorkspace } from "@/ui/home/EmployeeWorkspace";
import { ModulesGrid } from "@/ui/home/ModulesGrid";
import { useHomePersona } from "@/ui/home/persona";
import { NoAccessNotice, PlanGatedNotice } from "@/ui/home/WidgetStates";

const COUNT = new Intl.NumberFormat("en-IN");
const INVOICE_STATUSES = ["draft", "sent", "paid", "overdue", "void"] as const;
type InvoiceStatus = (typeof INVOICE_STATUSES)[number];
type SeriesKey = "revenue" | "received" | "outstanding";

const SERIES_OPTIONS: { value: SeriesKey; label: string }[] = [
  { value: "revenue", label: "Revenue" },
  { value: "received", label: "Received" },
  { value: "outstanding", label: "Outstanding" },
];

const STATUS_TONE: Record<InvoiceStatus, "positive" | "info" | "warning" | "danger" | "cyan"> = {
  paid: "positive",
  sent: "info",
  draft: "warning",
  overdue: "danger",
  void: "cyan",
};

interface InvoicesGroup {
  recent: FinanceInvoiceSummary[];
  statusCounts: Record<InvoiceStatus, number>;
  total: number;
}

/** `1 September 2026, 09:14` → its first word: the name a login is signed in under. */
function firstNameOf(fullName: string): string {
  const first = fullName.trim().split(/\s+/)[0];
  return first && first.length > 0 ? first : "there";
}

/** The mockup's own bands (dashboard audit §5.4): before noon, before five, and after — read from
 * the viewer's own clock. This is the one place on the platform a screen reads local time rather
 * than the organization's: a greeting is not a figure, and "good evening" at 6pm local is right
 * regardless of which timezone the business itself keeps its books in. */
function greetingFor(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function seriesValue(point: MonthlyRevenuePoint, key: SeriesKey): number {
  if (key === "revenue") return point.revenue_minor;
  if (key === "received") return point.received_minor;
  return point.outstanding_minor;
}

const SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Oct '25", "Nov", "Dec", "Jan '26" — a phone-width alternative to the server's own "Oct 2025",
 * short enough to survive a three-a-side axis (`categoryStride`) on a 375px card. The year marks
 * only the first point and wherever it actually changes, the way a wall calendar would. */
function compactMonthLabels(points: readonly MonthlyRevenuePoint[]): string[] {
  return points.map((point, index) => {
    const [year, monthNumber] = point.month.split("-");
    const name = SHORT_MONTHS[Number(monthNumber ?? "1") - 1] ?? point.label;
    const previousYear = points[index - 1]?.month.split("-")[0];
    return index === 0 || year !== previousYear ? `${name} '${(year ?? "").slice(2)}` : name;
  });
}

/** Below the brief's tablet boundary (768px) twelve month names need thinning to every third, not
 * the LineChart default's every second — a JS check because it drives label *content*
 * (`compactMonthLabels`), not only a class name a media query could switch on its own. */
function useNarrowViewport(): boolean {
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(max-width: 767px)").matches,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const list = window.matchMedia("(max-width: 767px)");
    const onChange = () => setNarrow(list.matches);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

function byDueDateSoonestFirst(a: ProjectTask, b: ProjectTask): number {
  if (a.due_date === null && b.due_date === null) return 0;
  if (a.due_date === null) return 1;
  if (b.due_date === null) return -1;
  return a.due_date.localeCompare(b.due_date);
}

async function loadInvoicesGroup(): Promise<InvoicesGroup> {
  const [recentPage, draft, sent, paid, overdue, voidPage] = await Promise.all([
    listFinanceInvoices({ size: 5 }),
    listFinanceInvoices({ status: "draft", size: 1 }),
    listFinanceInvoices({ status: "sent", size: 1 }),
    listFinanceInvoices({ status: "paid", size: 1 }),
    listFinanceInvoices({ status: "overdue", size: 1 }),
    listFinanceInvoices({ status: "void", size: 1 }),
  ]);
  const statusCounts: Record<InvoiceStatus, number> = {
    draft: draft.total,
    sent: sent.total,
    paid: paid.total,
    overdue: overdue.total,
    void: voidPage.total,
  };
  const total = INVOICE_STATUSES.reduce((sum, status) => sum + statusCounts[status], 0);
  return { recent: recentPage.items, statusCounts, total };
}

/** The KPI row's own loading shape — sized like `MetricCard` so nothing shifts once data lands. */
function KpiSkeleton({ label }: { label: string }) {
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6" role="status" aria-busy="true">
      <span className="sr-only">{`Loading ${label}`}</span>
      <Skeleton className="size-10 rounded-tile" />
      <Skeleton className="h-3 w-24 rounded-full" />
      <Skeleton className="h-8 w-28 rounded-tile" />
    </Card>
  );
}

/** The phone hero's own loading shape (mock-2's Total Revenue card) — wider and shorter than a
 *  grid tile's skeleton, so the hero doesn't jump once its real figure and bars land. */
function HeroSkeleton() {
  return (
    <div className="flex items-center gap-4 rounded-card bg-accent-soft p-4 shadow-card sm:p-6" role="status" aria-busy="true">
      <span className="sr-only">Loading total revenue</span>
      <Skeleton className="size-12 shrink-0 rounded-full" />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Skeleton className="h-3 w-24 rounded-full" />
        <Skeleton className="h-7 w-32 rounded-tile" />
        <Skeleton className="h-3 w-28 rounded-full" />
      </div>
      <Skeleton className="h-10 w-16 shrink-0 rounded-tile" />
    </div>
  );
}

interface KpiSlotProps {
  access: AccessState | null;
  state: "loading" | "error" | "ready";
  label: string;
  product: string;
  capability: string;
  canActivate: boolean;
  onStartTrial: () => void;
  onRetry: () => void;
  /** Overrides the default grid-tile skeleton — the hero uses its own, wider shape. */
  skeleton?: ReactNode;
  /** The gated notice's own size. Default `true` (a grid tile); the hero passes `false` for the
   *  full `EmptyState` its own full-width row has room for. */
  compact?: boolean;
  children: ReactNode;
}

/**
 * The one branch every KPI card shares: unknown grants, plan-gated, no grant, a failed fetch, or
 * the real value. `MetricCard`/`HeroMetricCard` themselves only ever draw the last of these — this
 * is what decides which one they get to.
 */
function KpiSlot({
  access,
  state,
  label,
  product,
  capability,
  canActivate,
  onStartTrial,
  onRetry,
  skeleton,
  compact = true,
  children,
}: KpiSlotProps) {
  if (access === null || state === "loading") return skeleton ?? <KpiSkeleton label={label} />;
  if (access === "plan_gated") {
    return <PlanGatedNotice product={product} canActivate={canActivate} onStartTrial={onStartTrial} compact={compact} />;
  }
  if (access === "no_access") return <NoAccessNotice capability={capability} compact={compact} />;
  if (state === "error") {
    return (
      <Card className="flex flex-col justify-center gap-3 p-4 sm:p-6">
        <p className="text-sm font-semibold text-ink">{`Couldn't load ${label.toLowerCase()}.`}</p>
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      </Card>
    );
  }
  return <>{children}</>;
}

export function HomePage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { status: grantsStatus, entitlements, grants } = usePermissions();

  const financeEntitled = entitlements?.includes("finance") ?? false;
  const crmEntitled = entitlements?.includes("crm") ?? false;
  const projectsEntitled = entitlements?.includes("projects") ?? false;

  const canReadFinanceSummary = useCan("finance.reports.summary.read", "ORG");
  const canReadFinanceInvoices = useCan("finance.billing.invoice.read", "ORG");
  const canReadFinanceCustomers = useCan("finance.billing.customer.read", "ORG");
  const canReadCrmCompanies = useCan("crm.book.company.read");
  const canReadCrmActivity = useCan("crm.pipeline.activity.read");
  const canReadTasks = useCan("projects.board.task.read");
  const canActivateProducts = useCan("core.product.activation.write", "ORG");

  const ready = grantsStatus === "ready";
  const revenueAccess = ready ? resolveAccess(financeEntitled, canReadFinanceSummary) : null;
  const invoicesAccess = ready ? resolveAccess(financeEntitled, canReadFinanceInvoices) : null;
  const customersAccess = ready ? resolveAccess(crmEntitled, canReadCrmCompanies) : null;
  const tasksAccess = ready ? resolveAccess(projectsEntitled, canReadTasks) : null;
  const activityAccess = ready ? resolveAccess(crmEntitled, canReadCrmActivity) : null;

  const overview = useAsync<FinanceOverview | null>(
    () => (revenueAccess === "ready" ? getFinanceOverview() : Promise.resolve(null)),
    [revenueAccess],
  );
  const invoices = useAsync<InvoicesGroup | null>(
    () => (invoicesAccess === "ready" ? loadInvoicesGroup() : Promise.resolve(null)),
    [invoicesAccess],
  );
  const financeCustomerNames = useAsync<Page<FinanceCustomerSummary> | null>(
    () => (invoicesAccess === "ready" && canReadFinanceCustomers ? listFinanceCustomers() : Promise.resolve(null)),
    [invoicesAccess, canReadFinanceCustomers],
  );
  const customersCount = useAsync<Page<Company> | null>(
    () => (customersAccess === "ready" ? countCrmCustomers() : Promise.resolve(null)),
    [customersAccess],
  );
  const tasks = useAsync<Page<ProjectTask> | null>(
    () => (tasksAccess === "ready" ? listMyTasks(100) : Promise.resolve(null)),
    [tasksAccess],
  );
  const dealActivity = useAsync<Page<DealActivity> | null>(
    () => (activityAccess === "ready" ? listDealActivities(5) : Promise.resolve(null)),
    [activityAccess],
  );

  const [seriesKey, setSeriesKey] = useState<SeriesKey>("revenue");
  const narrow = useNarrowViewport();

  const financeHost = getPlatformHost("finance");
  const projectsHost = getPlatformHost("projects");
  const goToProducts = () => navigate("/products");

  const firstName = firstNameOf(user?.full_name ?? "");
  const greetingWord = greetingFor(new Date().getHours());

  const customerName = (id: string): string =>
    financeCustomerNames.data?.items.find((row) => row.customer.id === id)?.customer.name ?? "Unknown customer";

  const invoiceColumns: Column<FinanceInvoiceSummary>[] = [
    {
      key: "number",
      header: "Invoice",
      render: (row) => <span className="font-semibold text-ink">{row.number}</span>,
      mobile: "title",
      cellClassName: "whitespace-nowrap",
    },
    { key: "customer", header: "Customer", render: (row) => customerName(row.customer_id), mobile: "field" },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      render: (row) => <span className="tnum">{formatMoneyWhole(row.total_minor, row.currency)}</span>,
      mobile: "field",
    },
    {
      key: "status",
      header: "Status",
      render: (row) =>
        isStatusWord(row.status) ? <StatusBadge status={row.status} /> : <StatusBadge tone="neutral" label={row.status} />,
      mobile: "status",
    },
    {
      key: "date",
      header: "Date",
      render: (row) => formatDate(row.issued_date),
      mobile: "field",
      cellClassName: "whitespace-nowrap",
    },
  ];

  /** The phone's Recent Invoices row: number and customer on the left, amount and status on the
   *  right — a compact list row, never the label/value fields `mobileCard="auto"` would build
   *  from the same columns. */
  function invoiceRow(row: FinanceInvoiceSummary) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-card bg-surface p-4 shadow-card">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="truncate text-sm font-semibold text-ink">{row.number}</span>
          <span className="truncate text-xs text-muted">{customerName(row.customer_id)}</span>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="tnum text-sm font-semibold text-ink">{formatMoneyWhole(row.total_minor, row.currency)}</span>
          {isStatusWord(row.status) ? <StatusBadge status={row.status} /> : <StatusBadge tone="neutral" label={row.status} />}
        </div>
      </div>
    );
  }

  const pendingTasks =
    tasksAccess === "ready" && tasks.status === "success" && tasks.data
      ? tasks.data.items.filter((task) => task.stage !== "done")
      : [];
  const upcomingTasks = [...pendingTasks].sort(byDueDateSoonestFirst).slice(0, 5);

  const invoiceEntries =
    invoicesAccess === "ready" && invoices.status === "success" && invoices.data
      ? invoices.data.recent.map((invoice) => ({
          id: `invoice-${invoice.id}`,
          content: `Invoice ${invoice.number} issued · ${formatMoneyWhole(invoice.total_minor, invoice.currency)}`,
          at: invoice.issued_date,
        }))
      : [];
  const activityEntries =
    activityAccess === "ready" && dealActivity.status === "success" && dealActivity.data
      ? dealActivity.data.items.map((activity) => ({
          id: `activity-${activity.id}`,
          content: activity.subject,
          at: activity.occurred_at,
        }))
      : [];
  const recentActivity = [...invoiceEntries, ...activityEntries]
    .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())
    .slice(0, 6);
  const activityHasSource = invoicesAccess === "ready" || activityAccess === "ready";
  const activityLoading =
    (invoicesAccess === "ready" && invoices.status === "loading") ||
    (activityAccess === "ready" && dealActivity.status === "loading");

  const history = overview.data?.revenue_history[0] ?? null;
  const months = history?.months ?? [];
  // The hero's real trailing months (mock-2's mini bars) — never a decorative series.
  const revenueBars = months.slice(-6).map((point) => point.revenue_minor);
  const overdueInvoices =
    invoicesAccess === "ready" && invoices.status === "success" && invoices.data ? invoices.data.statusCounts.overdue : 0;

  const customersCaption =
    scopeFor(grants ?? [], "crm.book.company.read") === "ORG"
      ? "All companies marked as customers"
      : "Your companies marked as customers";

  // A caller who holds none of this page's owner-level grants sees "My Workspace" instead
  // (mock-5's Employee View) — see `src/ui/home/persona.ts` for what counts as one.
  const persona = useHomePersona();
  if (persona === "employee") {
    return <EmployeeWorkspace />;
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={
          <>
            <span className="block sm:inline">{greetingWord},</span>{" "}
            <span className="block sm:inline">{firstName} 👋</span>
          </>
        }
        description="Here's what's happening with your business today."
        descriptionClassName="hidden sm:block"
        actions={
          <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card">
            <CalendarIcon className="size-4 text-muted" aria-hidden />
            This month
          </span>
        }
      />

      {/* KPI row. Below 768px: the hero Total Revenue card (mock-2, mock-5) and a 2×2 grid of
          Invoices, Customers, Pending Tasks and Overdue Invoices. From 768px: the mockup's four
          equal columns, Total Revenue among them like every other card. */}
      {narrow ? (
        <section aria-label="Key metrics" className="flex flex-col gap-4">
          <KpiSlot
            access={revenueAccess}
            state={overview.status === "success" ? "ready" : overview.status}
            label="Total Revenue"
            product="Finance"
            capability="revenue"
            canActivate={canActivateProducts}
            onStartTrial={goToProducts}
            onRetry={overview.reload}
            skeleton={<HeroSkeleton />}
            compact={false}
          >
            {overview.status === "success" ? (
              <HeroMetricCard
                label="Total Revenue"
                value={history ? formatMoneyKpi(history.revenue_current_minor, history.currency) : "0"}
                deltaPct={history?.revenue_delta_pct ?? undefined}
                caption={history?.revenue_delta_pct != null ? "from last month" : "This month"}
                icon={<TrendingUpIcon className="size-5" />}
                bars={revenueBars.length > 1 ? revenueBars : undefined}
              />
            ) : null}
          </KpiSlot>

          <div className="grid grid-cols-2 gap-4">
            <KpiSlot
              access={invoicesAccess}
              state={invoices.status === "success" ? "ready" : invoices.status}
              label="Total Invoices"
              product="Finance"
              capability="invoices"
              canActivate={canActivateProducts}
              onStartTrial={goToProducts}
              onRetry={invoices.reload}
            >
              {invoices.status === "success" && invoices.data ? (
                <MetricCard
                  size="compact"
                  label="Invoices"
                  value={COUNT.format(invoices.data.total)}
                  icon={<ReceiptIcon className="size-4" />}
                  tone="info"
                />
              ) : null}
            </KpiSlot>

            <KpiSlot
              access={customersAccess}
              state={customersCount.status === "success" ? "ready" : customersCount.status}
              label="CRM customers"
              product="CRM"
              capability="customers"
              canActivate={canActivateProducts}
              onStartTrial={goToProducts}
              onRetry={customersCount.reload}
            >
              {customersCount.status === "success" && customersCount.data ? (
                <MetricCard
                  size="compact"
                  label="CRM customers"
                  value={COUNT.format(customersCount.data.total)}
                  icon={<UsersIcon className="size-4" />}
                  tone="accent"
                />
              ) : null}
            </KpiSlot>

            <KpiSlot
              access={tasksAccess}
              state={tasks.status === "success" ? "ready" : tasks.status}
              label="Pending Tasks"
              product="Projects"
              capability="tasks"
              canActivate={canActivateProducts}
              onStartTrial={goToProducts}
              onRetry={tasks.reload}
            >
              {tasks.status === "success" ? (
                <MetricCard
                  size="compact"
                  label="Pending Tasks"
                  value={COUNT.format(pendingTasks.length)}
                  icon={<ClipboardCheckIcon className="size-4" />}
                  tone="warning"
                />
              ) : null}
            </KpiSlot>

            <KpiSlot
              access={invoicesAccess}
              state={invoices.status === "success" ? "ready" : invoices.status}
              label="Overdue Invoices"
              product="Finance"
              capability="invoices"
              canActivate={canActivateProducts}
              onStartTrial={goToProducts}
              onRetry={invoices.reload}
            >
              {invoices.status === "success" && invoices.data ? (
                <MetricCard
                  size="compact"
                  label="Overdue Invoices"
                  value={COUNT.format(overdueInvoices)}
                  icon={<AlertTriangleIcon className="size-4" />}
                  tone="danger"
                />
              ) : null}
            </KpiSlot>
          </div>
        </section>
      ) : (
        // Two columns from 768px through the desktop frame's own narrow end: the tablet rail (up
        // to 1024px) leaves each default-sized card only ~150px at four across, not enough room
        // for "₹12.5L" or "Suspended Accounts" without clipping — the visual harness's containment
        // check caught it. 1024px itself is no safer: the sidebar widens from the 72px rail to the
        // full 256px at that exact breakpoint, so a card would only trade one pinch for another.
        // Four across waits for xl (1280px), where there is real width to spare on both counts.
        <section aria-label="Key metrics" className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          <KpiSlot
            access={revenueAccess}
            state={overview.status === "success" ? "ready" : overview.status}
            label="Total Revenue"
            product="Finance"
            capability="revenue"
            canActivate={canActivateProducts}
            onStartTrial={goToProducts}
            onRetry={overview.reload}
          >
            {overview.status === "success" ? (
              <MetricCard
                label="Total Revenue"
                // Compact ("₹13.2L"), not the grouped figure the phone hero shows: a default-sized
                // card in this four-across row doesn't have the hero's full width to spare, and the
                // visual harness's own containment check caught the grouped form clipping here.
                value={history ? formatMoneyKpi(history.revenue_current_minor, history.currency) : "0"}
                trend={history?.revenue_delta_pct != null ? { value: history.revenue_delta_pct, period: "vs last month" } : undefined}
                caption={history?.revenue_delta_pct != null ? undefined : "This month"}
                icon={<TrendingUpIcon className="size-5" />}
                tone="positive"
              />
            ) : null}
          </KpiSlot>

          <KpiSlot
            access={invoicesAccess}
            state={invoices.status === "success" ? "ready" : invoices.status}
            label="Total Invoices"
            product="Finance"
            capability="invoices"
            canActivate={canActivateProducts}
            onStartTrial={goToProducts}
            onRetry={invoices.reload}
          >
            {invoices.status === "success" && invoices.data ? (
              <MetricCard
                label="Total Invoices"
                value={COUNT.format(invoices.data.total)}
                caption="All invoices issued"
                icon={<ReceiptIcon className="size-5" />}
                tone="info"
              />
            ) : null}
          </KpiSlot>

          <KpiSlot
            access={customersAccess}
            state={customersCount.status === "success" ? "ready" : customersCount.status}
            label="CRM customers"
            product="CRM"
            capability="customers"
            canActivate={canActivateProducts}
            onStartTrial={goToProducts}
            onRetry={customersCount.reload}
          >
            {customersCount.status === "success" && customersCount.data ? (
              <MetricCard
                label="CRM customers"
                value={COUNT.format(customersCount.data.total)}
                caption={customersCaption}
                icon={<UsersIcon className="size-5" />}
                tone="accent"
              />
            ) : null}
          </KpiSlot>

          <KpiSlot
            access={tasksAccess}
            state={tasks.status === "success" ? "ready" : tasks.status}
            label="Pending Tasks"
            product="Projects"
            capability="tasks"
            canActivate={canActivateProducts}
            onStartTrial={goToProducts}
            onRetry={tasks.reload}
          >
            {tasks.status === "success" ? (
              <MetricCard
                label="Pending Tasks"
                value={COUNT.format(pendingTasks.length)}
                caption="Assigned to you"
                icon={<ClipboardCheckIcon className="size-5" />}
                tone="warning"
              />
            ) : null}
          </KpiSlot>
        </section>
      )}

      <ModulesGrid />

      {/* Analytics — a large area chart of finance's own real 12-month history, and a donut of
          invoices by status (the honest form of the mockup's "Orders by status"; §12–13 of the
          brief this slice was scoped from). */}
      <section aria-label="Revenue and invoices" className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        {/* 3:2, not the equal-thirds a plain `grid-cols-3` gives: the donut's five-row legend
            (name, count, share) needs more than a third of the row to read without truncating. */}
        <div className="xl:col-span-3">
          <ChartCard
            title="Revenue Overview"
            description={overview.data?.period_label ? `Through ${overview.data.period_label}` : undefined}
            loading={revenueAccess === null || overview.status === "loading"}
            error={
              overview.status === "error" ? (
                <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
              ) : undefined
            }
            empty={
              revenueAccess === "plan_gated" ? (
                <PlanGatedNotice product="Finance" canActivate={canActivateProducts} onStartTrial={goToProducts} />
              ) : revenueAccess === "no_access" ? (
                <NoAccessNotice capability="revenue" />
              ) : overview.status === "success" && months.length === 0 ? (
                <EmptyState
                  title="No revenue history yet"
                  description="Revenue by month appears once invoices span two months."
                />
              ) : undefined
            }
            action={
              revenueAccess === "ready" && months.length > 0 ? (
                <Select
                  label="Series"
                  hideLabel
                  options={SERIES_OPTIONS}
                  value={seriesKey}
                  onChange={(event) => setSeriesKey(event.target.value as SeriesKey)}
                  containerClassName="w-36"
                />
              ) : undefined
            }
          >
            {overview.status === "success" && history && months.length > 0 ? (
              <LineChart
                label={`${SERIES_OPTIONS.find((option) => option.value === seriesKey)?.label ?? "Revenue"} by month`}
                categories={narrow ? compactMonthLabels(months) : months.map((point) => point.label)}
                categoryStride={narrow ? 3 : undefined}
                series={[
                  {
                    key: seriesKey,
                    label: SERIES_OPTIONS.find((option) => option.value === seriesKey)?.label ?? "Revenue",
                    values: months.map((point) => seriesValue(point, seriesKey)),
                  },
                ]}
                formatValue={(value) => formatMoneyWhole(value, history.currency)}
              />
            ) : null}
          </ChartCard>
        </div>

        <div className="xl:col-span-2">
          <InvoicesByStatusCard
            access={invoicesAccess}
            state={invoices.status}
            data={invoices.data}
            error={invoices.error}
            onRetry={invoices.reload}
            canActivate={canActivateProducts}
            onStartTrial={goToProducts}
            viewAllHref={`${financeHost}/invoices`}
          />
        </div>
      </section>

      {/* Operational — the mockup's Recent Orders and a Tasks checklist, both real. Top Products
          has no source (invoice lines carry a description only, with no product link; storefront
          has enquiries, not sales) and is not built, per the dashboard audit.
          Two columns only from 1536px: at 1024–1535 (this section's own `lg:grid-cols-2` used to
          switch here) the invoice table's Date column clipped its own value — half its card width
          isn't enough room for Invoice/Customer/Amount/Status/Date together, so it stays full
          width until there is. */}
      <section aria-label="Recent invoices and tasks" className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <SectionHeader
            title="Recent Invoices"
            action={
              invoicesAccess === "ready" ? (
                <a
                  href={`${financeHost}/invoices`}
                  className="hit-44 relative text-sm font-semibold text-accent-ink transition-colors hover:text-accent"
                >
                  View All
                </a>
              ) : undefined
            }
          />
          <DataTable
            columns={invoiceColumns}
            rows={invoicesAccess === "ready" && invoices.status === "success" && invoices.data ? invoices.data.recent : []}
            rowKey={(row) => row.id}
            loading={invoicesAccess === null || (invoicesAccess === "ready" && invoices.status === "loading")}
            errorState={
              invoicesAccess === "plan_gated" ? (
                <PlanGatedNotice product="Finance" canActivate={canActivateProducts} onStartTrial={goToProducts} />
              ) : invoicesAccess === "no_access" ? (
                <NoAccessNotice capability="invoices" />
              ) : invoicesAccess === "ready" && invoices.status === "error" ? (
                <ErrorState message={problemMessage(invoices.error)} onRetry={invoices.reload} />
              ) : undefined
            }
            emptyState={
              <EmptyState title="No invoices yet" description="Invoices you issue in Finance appear here." />
            }
            mobileCard={invoiceRow}
          />
        </Card>

        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <SectionHeader
            title="Tasks"
            action={
              tasksAccess === "ready" ? (
                <a
                  href={`${projectsHost}/board`}
                  className="hit-44 relative text-sm font-semibold text-accent-ink transition-colors hover:text-accent"
                >
                  View All
                </a>
              ) : undefined
            }
          />
          {tasksAccess === null || (tasksAccess === "ready" && tasks.status === "loading") ? (
            <div className="flex flex-col gap-2" role="status" aria-busy="true">
              <span className="sr-only">Loading tasks</span>
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : tasksAccess === "plan_gated" ? (
            <PlanGatedNotice product="Projects" canActivate={canActivateProducts} onStartTrial={goToProducts} />
          ) : tasksAccess === "no_access" ? (
            <NoAccessNotice capability="tasks" />
          ) : tasks.status === "error" ? (
            <ErrorState message={problemMessage(tasks.error)} onRetry={tasks.reload} />
          ) : upcomingTasks.length === 0 ? (
            <EmptyState title="Nothing pending" description="Tasks assigned to you appear here." />
          ) : (
            <ul className="flex list-none flex-col gap-1 p-0">
              {upcomingTasks.map((task) => (
                <li key={task.id} className="-mx-2 flex items-center gap-3 rounded-tile px-2 py-3">
                  <SquareIcon aria-hidden className="size-4 shrink-0 text-faint" />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-soft">{task.title}</span>
                  <StatusBadge status={task.priority} />
                  <span className="shrink-0 text-xs text-muted tnum">
                    {task.due_date ? formatDate(task.due_date) : "No due date"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {/* Insights — a cross-product feed composed from what this caller can actually read: real
          invoices and real CRM activity, each with a genuine timestamp. There is no cross-product
          activity endpoint (the dashboard audit's insights projection is a later slice), so a
          caller with neither source visible sees no card here rather than an empty promise. */}
      {activityHasSource ? (
        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <SectionHeader title="Recent Activity" />
          {activityLoading ? (
            <div className="flex flex-col gap-3" role="status" aria-busy="true">
              <span className="sr-only">Loading recent activity</span>
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-10 w-full rounded-tile" />
              ))}
            </div>
          ) : (
            <Timeline
              entries={recentActivity.map((entry) => ({
                id: entry.id,
                content: entry.content,
                timestamp: formatRelative(new Date(entry.at)),
                dateTime: new Date(entry.at).toISOString(),
              }))}
              emptyMessage="Nothing recent to show."
            />
          )}
        </Card>
      ) : null}
    </div>
  );
}

function InvoicesByStatusCard({
  access,
  state,
  data,
  error,
  onRetry,
  canActivate,
  onStartTrial,
  viewAllHref,
}: {
  access: AccessState | null;
  state: "loading" | "success" | "error";
  data: InvoicesGroup | null;
  error: Error | null;
  onRetry: () => void;
  canActivate: boolean;
  onStartTrial: () => void;
  viewAllHref: string;
}) {
  const slices: DonutSlice[] = data
    ? INVOICE_STATUSES.map((status) => ({
        key: status,
        label: statusLabel(status),
        value: data.statusCounts[status],
        tone: STATUS_TONE[status],
      }))
    : [];

  return (
    <ChartCard
      title="Invoices by Status"
      loading={access === null || state === "loading"}
      error={state === "error" ? <ErrorState message={problemMessage(error)} onRetry={onRetry} /> : undefined}
      empty={
        access === "plan_gated" ? (
          <PlanGatedNotice product="Finance" canActivate={canActivate} onStartTrial={onStartTrial} />
        ) : access === "no_access" ? (
          <NoAccessNotice capability="invoices" />
        ) : data && data.total === 0 ? (
          <EmptyState
            title="No invoices yet"
            description="Invoices by status appears once you've issued an invoice."
          />
        ) : undefined
      }
      skeleton="donut"
      action={
        access === "ready" && data && data.total > 0 ? (
          <a href={viewAllHref} className="hit-44 relative text-sm font-semibold text-accent-ink transition-colors hover:text-accent">
            View All
          </a>
        ) : undefined
      }
    >
      {data && data.total > 0 ? <DonutChart label="Invoices by status" slices={slices} /> : null}
    </ChartCard>
  );
}
