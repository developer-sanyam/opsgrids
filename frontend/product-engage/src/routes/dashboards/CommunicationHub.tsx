import { useState } from "react";
import type { ComponentType, SVGProps } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  BellIcon,
  Card,
  EmptyState,
  ICON_TILE,
  InboxIcon,
  MailIcon,
  MessageSquareIcon,
  PageHeader,
  PhoneIcon,
  SendIcon,
  Skeleton,
  SparkIcon,
  Tag,
  cn,
} from "@opsgrids/ui";
import type { Tone } from "@opsgrids/ui";

import { getHubOverview } from "@/api/analytics";
import type { ChannelKey, HubChannel, HubOverview } from "@/api/analytics";
import { formatNumber } from "@/lib/format";
import { ActionLink, LoadingStatus, TextLink } from "./DashboardParts";
import { DashboardFailure } from "./DashboardFailure";
import { DateRangeControl, DateRangeSummary } from "./DateRangeControl";
import { SectionTabs } from "./SectionTabs";
import { formatMoney } from "./format";
import { useDateRange } from "./useDateRange";

/**
 * The Communication hub (`GET /engage/analytics/overview`): which channels are connected, with a
 * way to connect the ones that are not, and both channels' headline numbers side by side.
 *
 * Connected or not is the service's answer (`channels[].connected`), never inferred from whether
 * any messages exist. SMS and push are listed because the service lists them, but Engage has no
 * connection flow for either, so they carry no Connect button — a button that led nowhere would be
 * worse than none. The cost is labelled **Estimated**, as it is everywhere it appears.
 */

interface ChannelMeta {
  name: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
  tone: Tone;
  /** Where a connected channel's own overview lives. */
  open: string | null;
  /** Where the connection is made. `null` where Engage has no such flow. */
  connect: string | null;
}

const CHANNELS: Record<ChannelKey, ChannelMeta> = {
  whatsapp: { name: "WhatsApp", Icon: MessageSquareIcon, tone: "positive", open: "/whatsapp", connect: "/whatsapp/connect" },
  email: { name: "Email", Icon: MailIcon, tone: "info", open: "/email", connect: "/connection" },
  sms: { name: "SMS", Icon: PhoneIcon, tone: "warning", open: null, connect: null },
  push: { name: "Push", Icon: BellIcon, tone: "accent", open: null, connect: null },
};

function ChannelCard({ channel }: { channel: HubChannel }) {
  const meta = CHANNELS[channel.key];
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6 transition-all hover:border-line-strong hover:shadow-sm">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={cn("grid size-11 shrink-0 place-items-center rounded-tile", ICON_TILE[meta.tone])}
        >
          <meta.Icon className="size-5" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h2 className="text-base font-bold text-ink">{meta.name}</h2>
          <div>
            <Tag tone={channel.connected ? "positive" : "neutral"}>
              {channel.connected ? "Connected" : "Not connected"}
            </Tag>
          </div>
        </div>
      </div>
      <p className="min-h-10 text-sm text-muted">
        {channel.detail ??
          (channel.connected
          ? "Connected."
          : meta.connect === null
            ? ""
            : `Connect ${meta.name} to start messaging customers.`)}
      </p>
      {channel.connected ? (
        meta.open !== null ? (
          <div>
            <ActionLink to={meta.open} variant="secondary">
              Open {meta.name}
            </ActionLink>
          </div>
        ) : null
      ) : meta.connect !== null ? (
        <div>
          <ActionLink to={meta.connect}>Connect {meta.name}</ActionLink>
        </div>
      ) : (
        <p className="text-sm text-muted">Connecting {meta.name} isn&rsquo;t available in Engage yet.</p>
      )}
    </Card>
  );
}

function ChannelSkeleton() {
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <div className="flex items-start gap-3">
        <Skeleton className="size-11 rounded-tile" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-5 w-28" />
        </div>
      </div>
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-11 w-36" />
    </Card>
  );
}

function Figure({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <dt className="break-words text-xs font-bold uppercase tracking-wider text-faint">{label}</dt>
      <dd className="tnum text-xl font-bold text-ink">{formatNumber(value)}</dd>
    </div>
  );
}

function RateBar({
  label,
  numerator,
  denominator,
  tone = "positive",
}: {
  label: string;
  numerator: number;
  denominator: number;
  tone?: "positive" | "info" | "accent";
}) {
  const percent = denominator > 0 ? Math.min(100, Math.round((numerator / denominator) * 100)) : 0;
  const barColors = {
    positive: "bg-positive",
    info: "bg-info",
    accent: "bg-accent",
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-muted">{label}</span>
        <span className="font-mono font-bold text-ink">{percent}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-muted">
        <div
          className={cn("h-full transition-all duration-300", barColors[tone])}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function ChannelSummary({
  name,
  to,
  sent,
  figures,
  deliveryRate,
  engagementRate,
  engagementLabel,
}: {
  name: string;
  to: string;
  sent: number;
  figures: { label: string; value: number }[];
  deliveryRate?: { num: number; den: number };
  engagementRate?: { num: number; den: number };
  engagementLabel?: string;
}) {
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6 transition-all hover:border-line-strong">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-bold text-ink">{name} in this period</h2>
        <TextLink to={to}>See overview</TextLink>
      </div>
      {sent === 0 ? (
        <EmptyState
          title={`No ${name} messages in this period`}
          description="Try a wider date range, or send a message to see figures here."
        />
      ) : (
        <div className="flex flex-col gap-5">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            {figures.map((figure) => (
              <Figure key={figure.label} label={figure.label} value={figure.value} />
            ))}
          </dl>

          {deliveryRate && deliveryRate.den > 0 ? (
            <div className="grid grid-cols-1 gap-3 border-t border-line pt-3 sm:grid-cols-2">
              <RateBar
                label="Delivery Rate"
                numerator={deliveryRate.num}
                denominator={deliveryRate.den}
                tone="positive"
              />
              {engagementRate && (
                <RateBar
                  label={engagementLabel || "Read Rate"}
                  numerator={engagementRate.num}
                  denominator={engagementRate.den}
                  tone="info"
                />
              )}
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/** Operational HUD Strip: Real-time intelligence and connectivity status */
function OperationalHud({ data }: { data: HubOverview }) {
  const isWhatsAppConnected = data.channels.some((c) => c.key === "whatsapp" && c.connected);

  const activeAutomations = data.whatsapp?.totals?.active_automations ?? 0;
  const activeConversations = data.whatsapp?.totals?.active_conversations ?? 0;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {/* 1. Live WhatsApp Gateway Pulse */}
      <div className="flex items-center justify-between rounded-card border border-line bg-surface p-3.5 shadow-xs">
        <div className="flex items-center gap-3">
          <span className="relative flex size-3">
            {isWhatsAppConnected ? (
              <>
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-positive opacity-75" />
                <span className="relative inline-flex size-3 rounded-full bg-positive" />
              </>
            ) : (
              <span className="relative inline-flex size-3 rounded-full bg-muted" />
            )}
          </span>
          <div className="flex flex-col">
            <span className="text-xs font-bold text-ink">WhatsApp Gateway</span>
            <span className="text-caption text-muted">
              {isWhatsAppConnected ? "Live Socket Active" : "Standby / Not Linked"}
            </span>
          </div>
        </div>
        <Link
          to="/whatsapp/connection"
          className="text-xs font-semibold text-accent hover:text-accent-strong hover:underline"
        >
          {isWhatsAppConnected ? "Settings" : "Pair QR"}
        </Link>
      </div>

      {/* 2. Automated AI Engine */}
      <div className="flex items-center justify-between rounded-card border border-line bg-surface p-3.5 shadow-xs">
        <div className="flex items-center gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
            <SparkIcon className="size-4" />
          </span>
          <div className="flex flex-col">
            <span className="text-xs font-bold text-ink">AI Auto-Responder</span>
            <span className="text-caption text-muted">
              {activeAutomations > 0 ? `${activeAutomations} Active Automations` : "Auto-Reply Configured"}
            </span>
          </div>
        </div>
        <Link
          to="/whatsapp/connection"
          className="text-xs font-semibold text-accent hover:text-accent-strong hover:underline"
        >
          Configure
        </Link>
      </div>

      {/* 3. 24h Customer Service Window */}
      <div className="flex items-center justify-between rounded-card border border-line bg-surface p-3.5 shadow-xs">
        <div className="flex items-center gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-tile bg-positive-soft text-positive">
            <InboxIcon className="size-4" />
          </span>
          <div className="flex flex-col">
            <span className="text-xs font-bold text-ink">Meta 24h Window</span>
            <span className="text-caption text-muted">
              {activeConversations > 0 ? `${activeConversations} Open Conversations` : "Ready in Inbox"}
            </span>
          </div>
        </div>
        <Link
          to="/inbox"
          className="text-xs font-semibold text-accent hover:text-accent-strong hover:underline"
        >
          Open Inbox
        </Link>
      </div>
    </div>
  );
}

function Performance({
  data,
  filterChannel,
}: {
  data: HubOverview;
  filterChannel: "all" | "whatsapp" | "email";
}) {
  const connected = (key: ChannelKey) => data.channels.some((c) => c.key === key && c.connected);
  const whatsapp = connected("whatsapp") ? data.whatsapp : null;
  const email = connected("email") ? data.email : null;

  if (whatsapp === null && email === null) {
    return (
      <Card className="p-6">
        <EmptyState
          title="Nothing to report yet"
          description="Connect WhatsApp or email above. Once you send, how each channel is doing appears here."
        />
      </Card>
    );
  }

  const showWhatsApp = filterChannel === "all" || filterChannel === "whatsapp";
  const showEmail = filterChannel === "all" || filterChannel === "email";

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {showWhatsApp && whatsapp !== null ? (
        <ChannelSummary
          name="WhatsApp"
          to="/whatsapp"
          sent={whatsapp.sent}
          figures={[
            { label: "Sent", value: whatsapp.sent },
            { label: "Delivered", value: whatsapp.delivered },
            { label: "Read", value: whatsapp.read },
            { label: "Responses", value: whatsapp.replies },
            { label: "Failed", value: whatsapp.failed },
          ]}
          deliveryRate={{ num: whatsapp.delivered, den: whatsapp.sent }}
          engagementRate={{ num: whatsapp.read, den: whatsapp.sent }}
          engagementLabel="Read Rate"
        />
      ) : null}
      {showEmail && email !== null ? (
        <ChannelSummary
          name="Email"
          to="/email"
          sent={email.sent}
          figures={[
            { label: "Sent", value: email.sent },
            { label: "Delivered", value: email.delivered },
            { label: "Opened", value: email.opened },
            { label: "Clicked", value: email.clicked },
            { label: "Failed", value: email.failed },
          ]}
          deliveryRate={{ num: email.delivered, den: email.sent }}
          engagementRate={{ num: email.opened, den: email.sent }}
          engagementLabel="Open Rate"
        />
      ) : null}
    </div>
  );
}

function CostCard({ data }: { data: HubOverview }) {
  if (data.costs === null) return null;
  return (
    <Card className="flex flex-col gap-2 p-4 sm:p-6 transition-all hover:border-line-strong">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold text-ink">Estimated messaging cost</h2>
        <Tag tone="warning">Estimated</Tag>
      </div>
      <p className="tnum text-2xl font-bold text-ink">{formatMoney(data.costs.total)}</p>
      <p className="text-sm text-muted">
        Estimated from the rate card on file, not from a provider invoice.
      </p>
    </Card>
  );
}

export function CommunicationHubPage() {
  const dates = useDateRange();
  const { from, to } = dates.range;
  const state = useAsync(() => getHubOverview({ from, to }), [from, to]);
  const data = state.status === "success" ? state.data : null;
  const [filterChannel, setFilterChannel] = useState<"all" | "whatsapp" | "email">("all");

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Engage"
        title="Communication"
        description="Which channels are connected, and how WhatsApp and email are doing."
        meta={<DateRangeSummary controls={dates} />}
        actions={<DateRangeControl controls={dates} />}
      />
      <SectionTabs section="hub" />
      <LoadingStatus status={state.status} what="communication overview" />

      {state.status === "error" ? (
        <DashboardFailure
          error={state.error}
          title="Couldn't load the communication overview"
          what="the communication overview"
          onRetry={state.reload}
        />
      ) : data === null ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <ChannelSkeleton key={index} />
          ))}
        </div>
      ) : (
        <>
          {/* Bento Row 1: Operational Health HUD */}
          <OperationalHud data={data} />

          {/* Quick Action & Channel Focus Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-line bg-surface p-3">
            <div className="flex items-center gap-1.5">
              <span className="mr-1 text-caption font-bold text-muted">Filter:</span>
              <button
                type="button"
                onClick={() => setFilterChannel("all")}
                className={cn(
                  "rounded-chip px-3 py-1 text-xs font-semibold transition-colors",
                  filterChannel === "all"
                    ? "bg-accent text-on-accent"
                    : "bg-surface-muted text-muted hover:text-ink",
                )}
              >
                All Channels
              </button>
              <button
                type="button"
                onClick={() => setFilterChannel("whatsapp")}
                className={cn(
                  "rounded-chip px-3 py-1 text-xs font-semibold transition-colors",
                  filterChannel === "whatsapp"
                    ? "bg-accent text-on-accent"
                    : "bg-surface-muted text-muted hover:text-ink",
                )}
              >
                WhatsApp
              </button>
              <button
                type="button"
                onClick={() => setFilterChannel("email")}
                className={cn(
                  "rounded-chip px-3 py-1 text-xs font-semibold transition-colors",
                  filterChannel === "email"
                    ? "bg-accent text-on-accent"
                    : "bg-surface-muted text-muted hover:text-ink",
                )}
              >
                Email
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Link
                to="/campaigns"
                className="inline-flex h-8 items-center gap-1.5 rounded-chip border border-line bg-surface px-3 text-xs font-semibold text-ink shadow-xs hover:bg-surface-muted"
              >
                <SendIcon className="size-3.5" />
                Campaigns
              </Link>
              <Link
                to="/inbox"
                className="inline-flex h-8 items-center gap-1.5 rounded-chip bg-accent px-3 text-xs font-semibold text-on-accent shadow-xs hover:bg-accent-strong"
              >
                <InboxIcon className="size-3.5" />
                Live Inbox
              </Link>
            </div>
          </div>

          {/* Bento Row 2: Channel Connection Cards */}
          <section aria-label="Channels" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {data.channels.map((channel) => (
              <ChannelCard key={channel.key} channel={channel} />
            ))}
          </section>

          {/* Bento Row 3: Performance & Cost */}
          <Performance data={data} filterChannel={filterChannel} />
          <CostCard data={data} />
        </>
      )}
    </div>
  );
}
