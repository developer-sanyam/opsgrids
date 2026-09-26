import type { ComponentType, ReactNode, SVGProps } from "react";
import { Link } from "react-router-dom";

import {
  ActivityIcon,
  Card,
  ChartCard,
  CheckCircleIcon,
  EmptyState,
  FileTextIcon,
  ICON_TILE,
  MailIcon,
  MessageIcon,
  MetricCard,
  SendIcon,
  Skeleton,
  SparkIcon,
  UserPlusIcon,
  cn,
  formatRelative,
} from "@opsgrids/ui";
import type { Tone } from "@opsgrids/ui";

import type { RecentActivity } from "@/api/analytics";
import { formatInstant } from "@/lib/format";

/**
 * The pieces the WhatsApp overview, the email overview and the hub have in common: a row of KPI
 * cards that keeps its size while it loads, the recent-activity list, and the link styled as the
 * button it is. Nothing here fetches.
 */

export interface Kpi {
  key: string;
  label: string;
  /** Pre-formatted. */
  value: string;
  icon: ReactNode;
  tone: Tone;
  caption?: string | undefined;
}

const COLUMNS = {
  3: "grid grid-cols-1 gap-4 sm:grid-cols-3",
  4: "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4",
  5: "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5",
} as const;

/**
 * A row of KPI cards. `kpis === null` is the loading state: the same number of cards, the same
 * height, so nothing below moves when the figures arrive.
 */
export function KpiGrid({
  label,
  kpis,
  columns,
  compact = false,
}: {
  /** The section's accessible name: "Message totals". */
  label: string;
  kpis: Kpi[] | null;
  columns: keyof typeof COLUMNS;
  compact?: boolean;
}) {
  return (
    <section aria-label={label} className={COLUMNS[columns]}>
      {kpis === null
        ? Array.from({ length: columns }).map((_, index) => (
            <Card key={index} className="flex flex-col gap-2 p-4 sm:p-6">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-8 w-20" />
              <Skeleton className="h-4 w-32" />
            </Card>
          ))
        : kpis.map((kpi) => (
            <MetricCard
              key={kpi.key}
              label={kpi.label}
              value={kpi.value}
              icon={kpi.icon}
              tone={kpi.tone}
              size={compact ? "compact" : "default"}
              {...(kpi.caption !== undefined ? { caption: kpi.caption } : {})}
            />
          ))}
    </section>
  );
}

const ACTION_LINK =
  "inline-flex h-11 items-center justify-center gap-2 rounded-tile px-4 text-sm font-semibold transition-colors " +
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** A link that looks like a button, for an action that is navigation. `Button` renders a
 *  `<button>`, and a button that navigates is not one a person can open in a new tab. */
export function ActionLink({
  to,
  variant = "primary",
  children,
}: {
  to: string;
  variant?: "primary" | "secondary";
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      className={cn(
        ACTION_LINK,
        variant === "primary"
          ? "bg-accent text-on-accent hover:bg-accent-strong active:bg-accent-strong"
          : "border border-field-edge bg-surface text-ink hover:bg-sunken active:bg-sunken",
      )}
    >
      {children}
    </Link>
  );
}

/** A quiet text link inside a card: "View all". */
export function TextLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex h-9 items-center rounded-tile px-2 text-sm font-semibold text-accent-ink transition-colors hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {children}
    </Link>
  );
}

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

/** The icon and family a feed line is drawn in. The kinds email names are exact; WhatsApp's `kind`
 *  is an open string, so it is matched by what it mentions and anything else is a plain event. */
function activityVisual(kind: string): { Icon: IconType; tone: Tone } {
  if (kind === "campaign_sent") return { Icon: SendIcon, tone: "positive" };
  if (kind === "high_open_rate") return { Icon: MailIcon, tone: "info" };
  if (kind === "new_subscriber") return { Icon: UserPlusIcon, tone: "warning" };
  if (kind.includes("automation")) return { Icon: SparkIcon, tone: "accent" };
  if (kind.includes("template")) return { Icon: FileTextIcon, tone: "warning" };
  if (kind.includes("deliver")) return { Icon: CheckCircleIcon, tone: "positive" };
  if (kind.includes("message") || kind.includes("reply")) return { Icon: MessageIcon, tone: "info" };
  return { Icon: ActivityIcon, tone: "accent" };
}

/**
 * The feed beside the volume chart. Loading, empty and populated share one card, so the grid does
 * not jump; the timestamp is relative with the exact instant on hover and in the `<time>` element.
 */
export function ActivityCard({
  items,
  viewAll,
  emptyDescription,
  loading = false,
}: {
  items: RecentActivity[];
  viewAll: { to: string; label: string };
  emptyDescription: string;
  loading?: boolean;
}) {
  return (
    <ChartCard
      title="Recent activity"
      loading={loading}
      skeleton="bar"
      action={<TextLink to={viewAll.to}>{viewAll.label}</TextLink>}
      empty={
        items.length === 0 ? (
          <EmptyState
            icon={<ActivityIcon className="size-6" />}
            title="No recent activity yet"
            description={emptyDescription}
          />
        ) : undefined
      }
    >
      <ul className="flex flex-col">
        {items.map((item, index) => {
          const { Icon, tone } = activityVisual(item.kind);
          return (
            <li
              key={`${item.at}-${index}`}
              className="flex items-center gap-3 border-t border-line py-3 first:border-t-0 first:pt-0"
            >
              <span
                aria-hidden
                className={cn("grid size-10 shrink-0 place-items-center rounded-full", ICON_TILE[tone])}
              >
                <Icon className="size-4.5" />
              </span>
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="text-sm font-semibold text-ink">{item.title}</span>
                <span className="truncate text-sm text-muted">{item.text}</span>
              </div>
              <time dateTime={item.at} title={formatInstant(item.at)} className="shrink-0 text-xs text-muted">
                {formatRelative(item.at)}
              </time>
            </li>
          );
        })}
      </ul>
    </ChartCard>
  );
}

/** Screen readers hear that a dashboard is loading and when it is done; sighted users see the
 *  skeletons. Without this a refetch after a range change is silent. */
export function LoadingStatus({ loading, what }: { loading: boolean; what: string }) {
  return (
    <p role="status" className="sr-only">
      {loading ? `Loading ${what}` : `${what} loaded`}
    </p>
  );
}
