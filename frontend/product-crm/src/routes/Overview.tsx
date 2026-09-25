import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { getAllDeals, getSummary, listActivities, listLeads } from "@/api/client";
import type { Deal, LeadStatus } from "@/api/types";
import { scopeFor, useAsync, useCan, usePermissions } from "@opsgrids/client";
import type { Scope } from "@opsgrids/client";
import { useDirectory } from "@/data/directoryContext";
import { needsAttention } from "@/data/derive";
import { getMySubjectRef } from "@/lib/subject";
import { formatMoneyCompact, nowIso } from "@/lib/format";
import {
  ActivityTimeline,
  BarList,
  Button,
  Card,
  CardSkeleton,
  ChartCard,
  DonutChart,
  EmptyState,
  ErrorState,
  MetricCard,
  NeedsAttention,
  PageHeader,
  SectionHeader,
  Skeleton,
  Switch,
  TABLET_QUERY,
  useMediaQuery,
} from "@/ui";
import type { BarListItem, DonutSlice } from "@/ui";
import {
  BanknoteIcon,
  HandshakeIcon,
  PlusIcon,
  TargetIcon,
  UserPlusIcon,
  useToast,
} from "@opsgrids/ui";
import { DealFormDialog } from "@/forms/DealFormDialog";

const COUNT = new Intl.NumberFormat("en-IN");
const EMPTY_DEALS: Deal[] = [];

/** The five lead statuses, in intake order — one `Page.total` call each. Together they add up to
 *  the whole book, so "Leads" and "Leads by status" share these five calls rather than a sixth
 *  unfiltered one. */
const LEAD_STATUSES: LeadStatus[] = ["new", "working", "qualified", "converted", "disqualified"];

const LEAD_STATUS_LABEL: Record<LeadStatus, string> = {
  new: "New",
  working: "Working",
  qualified: "Qualified",
  converted: "Converted",
  disqualified: "Disqualified",
};

const LEAD_STATUS_TONE: Record<LeadStatus, DonutSlice["tone"]> = {
  new: "info",
  working: "accent",
  qualified: "warning",
  converted: "positive",
  disqualified: "cyan",
};

interface LeadStats {
  total: number;
  converted: number;
  counts: Record<LeadStatus, number>;
}

/** Five `size=1` calls, scoped to one rep when `ownerId` is given — cheap, and exact at any book
 *  size, unlike counting a capped page. */
async function loadLeadStats(ownerId: string | undefined): Promise<LeadStats> {
  const pages = await Promise.all(
    LEAD_STATUSES.map((status) => listLeads({ status, size: 1, ...(ownerId ? { ownerId } : {}) })),
  );
  const counts = Object.fromEntries(
    LEAD_STATUSES.map((status, index) => [status, pages[index]?.total ?? 0]),
  ) as Record<LeadStatus, number>;
  const total = LEAD_STATUSES.reduce((sum, status) => sum + counts[status], 0);
  return { total, converted: counts.converted, counts };
}

/** "All {subject}" for an ORG-scoped reader, "Your {subject}" for an OWN-scoped one or once the
 *  "My pipeline" toggle narrows it by hand — a KPI or chart must never read as company-wide when
 *  it is a slice (dashboard audit §2.4). */
function scopedCaption(subject: string, mine: boolean, scope: Scope | null): string {
  if (mine || scope !== "ORG") return `Your ${subject}`;
  return `All ${subject}`;
}

interface OverviewHeaderProps {
  onCreate: () => void;
  canCreate: boolean;
  canToggleMine: boolean;
  mine: boolean;
  onToggleMine: (next: boolean) => void;
}

function OverviewHeader({ onCreate, canCreate, canToggleMine, mine, onToggleMine }: OverviewHeaderProps) {
  return (
    <PageHeader
      title="Overview"
      description="Your pipeline, leads and follow-ups."
      actions={
        <>
          {canToggleMine ? (
            <Switch
              checked={mine}
              onChange={onToggleMine}
              label="My pipeline"
              description={mine ? "Showing your own deals and leads" : "Showing the whole pipeline"}
            />
          ) : null}
          {canCreate ? (
            <Button size="md" leadingIcon={<PlusIcon className="size-4" />} onClick={onCreate}>
              New deal
            </Button>
          ) : null}
        </>
      }
    />
  );
}

/** The KPI row's shared branch: a sized skeleton while its own source loads, a generic
 *  couldn't-load-plus-retry if it failed, or the real card. One widget's failure never blanks the
 *  other three (dashboard audit §5.0.7). */
function MetricSlot({
  status,
  onRetry,
  label,
  children,
}: {
  status: "loading" | "error" | "success";
  onRetry: () => void;
  label: string;
  children: ReactNode;
}) {
  if (status === "loading") return <CardSkeleton lines={1} label={`Loading ${label}`} />;
  if (status === "error") {
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

export function OverviewPage() {
  const toast = useToast();
  const directory = useDirectory();
  const { grants } = usePermissions();
  const canCreateDeal = useCan("crm.pipeline.deal.write", "OWN");
  const [creating, setCreating] = useState(false);
  const [mine, setMine] = useState(false);

  const mySubjectRef = useMemo(() => getMySubjectRef(), []);
  const dealScope = scopeFor(grants ?? [], "crm.pipeline.deal.read");
  const leadScope = scopeFor(grants ?? [], "crm.lead.lead.read");
  const canToggleMine = dealScope === "ORG" && mySubjectRef !== null;
  const scopedOwnerId = mine && mySubjectRef !== null ? mySubjectRef : undefined;

  const wide = useMediaQuery(TABLET_QUERY, true);
  const metricSize = wide ? "default" : "compact";

  const summary = useAsync(() => getSummary(mine ? { owner: "me" } : {}), [mine]);
  const deals = useAsync(
    () => getAllDeals(scopedOwnerId ? { ownerId: scopedOwnerId } : {}),
    [scopedOwnerId],
  );
  const leadStats = useAsync(() => loadLeadStats(scopedOwnerId), [scopedOwnerId]);
  const activities = useAsync(useCallback(() => listActivities({ size: 6 }), []), []);

  const items = deals.status === "success" ? deals.data.items : EMPTY_DEALS;
  const today = nowIso().slice(0, 10);
  const currentMonth = nowIso().slice(0, 7);
  const currency = summary.status === "success" ? summary.data.currency : "INR";

  const attention = useMemo(() => needsAttention(items, today).slice(0, 6), [items, today]);
  const wonThisMonth = useMemo(() => {
    const rows = items.filter((d) => d.stage === "closed_won" && d.close_date.slice(0, 7) === currentMonth);
    return { count: rows.length, valueMinor: rows.reduce((sum, d) => sum + d.value_minor, 0) };
  }, [items, currentMonth]);
  const conversionPct =
    leadStats.status === "success" && leadStats.data.total > 0
      ? Math.round((leadStats.data.converted / leadStats.data.total) * 100)
      : 0;

  const funnelItems: BarListItem[] =
    summary.status === "success"
      ? summary.data.funnel.map((row) => ({
          key: row.stage,
          label: `${row.label} (${row.count})`,
          value: row.value_minor,
        }))
      : [];
  const leadSlices: DonutSlice[] =
    leadStats.status === "success"
      ? LEAD_STATUSES.map((status) => ({
          key: status,
          label: LEAD_STATUS_LABEL[status],
          value: leadStats.data.counts[status],
          tone: LEAD_STATUS_TONE[status],
        }))
      : [];

  const recentActivities = activities.status === "success" ? activities.data.items : [];

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader
        onCreate={() => setCreating(true)}
        canCreate={canCreateDeal}
        canToggleMine={canToggleMine}
        mine={mine}
        onToggleMine={setMine}
      />

      {/* Row 1 — KPI cards: leads, open pipeline, won this month, conversion. Every delta this
          row could show needs a comparison the server doesn't yet keep (dashboard audit §3.3
          rows 2, 9); none is invented, so none of these four cards carries a trend.
          Four across waits for xl (1280px): at 768-1279 a default-sized card only gets ~170px,
          not enough for a compact money value like "₹1.9 Cr" without clipping (same fix as the
          dashboard's own KPI row). */}
      <section aria-label="Key metrics" className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <MetricSlot status={leadStats.status} onRetry={leadStats.reload} label="Leads">
          {leadStats.status === "success" ? (
            <MetricCard
              label="Leads"
              value={COUNT.format(leadStats.data.total)}
              icon={<UserPlusIcon className="size-5" />}
              tone="cyan"
              size={metricSize}
            />
          ) : null}
        </MetricSlot>

        <MetricSlot status={summary.status} onRetry={summary.reload} label="Open pipeline">
          {summary.status === "success" ? (
            <MetricCard
              label="Open Pipeline"
              value={formatMoneyCompact(summary.data.pipeline_value_minor, summary.data.currency)}
              icon={<BanknoteIcon className="size-5" />}
              tone="accent"
              size={metricSize}
            />
          ) : null}
        </MetricSlot>

        <MetricSlot status={deals.status} onRetry={deals.reload} label="Won this month">
          {deals.status === "success" ? (
            <MetricCard
              label="Won This Month"
              value={formatMoneyCompact(wonThisMonth.valueMinor, currency)}
              icon={<HandshakeIcon className="size-5" />}
              tone="positive"
              size={metricSize}
            />
          ) : null}
        </MetricSlot>

        <MetricSlot status={leadStats.status} onRetry={leadStats.reload} label="Conversion">
          {leadStats.status === "success" ? (
            <MetricCard
              label="Lead Conversion"
              value={`${conversionPct}%`}
              icon={<TargetIcon className="size-5" />}
              tone="info"
              size={metricSize}
            />
          ) : null}
        </MetricSlot>
      </section>

      {/* Row 2 — pipeline by stage · leads by status */}
      <section aria-label="Pipeline and leads" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <ChartCard
            title="Pipeline by Stage"
            description={scopedCaption("open deals, by stage", mine, dealScope)}
            loading={summary.status === "loading"}
            skeleton="bar"
            error={
              summary.status === "error" ? (
                <ErrorState message={summary.error.message} onRetry={summary.reload} />
              ) : undefined
            }
            empty={
              summary.status === "success" && funnelItems.every((item) => item.value === 0) ? (
                <EmptyState
                  title="No open deals yet"
                  description="Add a deal to see the pipeline fill in here."
                />
              ) : undefined
            }
          >
            {summary.status === "success" ? (
              <BarList label="Open pipeline value by stage" items={funnelItems} formatValue={(v) => formatMoneyCompact(v, summary.data.currency)} />
            ) : null}
          </ChartCard>
        </div>

        <div className="xl:col-span-1">
          <ChartCard
            title="Leads by Status"
            description={scopedCaption("leads, by status", mine, leadScope)}
            loading={leadStats.status === "loading"}
            skeleton="donut"
            error={
              leadStats.status === "error" ? (
                <ErrorState message={leadStats.error.message} onRetry={leadStats.reload} />
              ) : undefined
            }
            empty={
              leadStats.status === "success" && leadStats.data.total === 0 ? (
                <EmptyState
                  title="No leads yet"
                  description="Leads you capture appear here, broken down by status."
                />
              ) : undefined
            }
          >
            {leadStats.status === "success" ? <DonutChart label="Leads by status" slices={leadSlices} size="sm" /> : null}
          </ChartCard>
        </div>
      </section>

      {/* Row 3 — follow-ups due · recent activity */}
      <section aria-label="Follow-ups" className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
        <Card className="flex flex-col gap-2 p-6 xl:col-span-2">
          <SectionHeader
            title="Needs Attention"
            subtitle="Overdue, closing soon, or going quiet"
            action={
              <Link
                to="/deals"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                All deals
              </Link>
            }
          />
          {deals.status === "loading" ? (
            <div className="flex flex-col gap-3 py-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {deals.status === "error" ? (
            <ErrorState message={deals.error.message} onRetry={deals.reload} />
          ) : null}
          {deals.status === "success" ? <NeedsAttention items={attention} /> : null}
        </Card>

        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader
            title="Recent Activity"
            action={
              <Link
                to="/activities"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                View all
              </Link>
            }
          />
          {activities.status === "loading" ? (
            <div className="flex flex-col gap-4 py-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {activities.status === "error" ? (
            <ErrorState message={activities.error.message} onRetry={activities.reload} />
          ) : null}
          {activities.status === "success" ? <ActivityTimeline activities={recentActivities} /> : null}
        </Card>
      </section>

      {creating ? (
        <DealFormDialog
          deal={null}
          companies={directory.companies}
          contacts={directory.contacts}
          owners={directory.owners}
          onOwnerCreated={() => directory.reload()}
          onClose={() => setCreating(false)}
          onSaved={(deal) => {
            setCreating(false);
            toast.success(`${deal.name} created.`);
            deals.reload();
            summary.reload();
          }}
        />
      ) : null}
    </div>
  );
}
