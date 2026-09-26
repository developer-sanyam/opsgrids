import type { ComponentType, SVGProps } from "react";

import { useAsync } from "@opsgrids/client";
import {
  BellIcon,
  Card,
  EmptyState,
  ICON_TILE,
  MailIcon,
  MessageSquareIcon,
  PageHeader,
  PhoneIcon,
  Skeleton,
  Tag,
  cn,
} from "@opsgrids/ui";
import type { Tone } from "@opsgrids/ui";

import { getHubOverview } from "@/api/analytics";
import type { ChannelKey, HubChannel, HubOverview } from "@/api/analytics";
import { formatNumber } from "@/lib/format";
import { ActionLink, LoadingStatus, TextLink } from "./DashboardParts";
import { DashboardFailure } from "./DashboardFailure";
import { DateRangeControl } from "./DateRangeControl";
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
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
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
          (channel.connected ? "Connected." : `Connect ${meta.name} to start messaging customers.`)}
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
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-bold uppercase tracking-wider text-faint">{label}</dt>
      <dd className="tnum text-xl font-bold text-ink">{formatNumber(value)}</dd>
    </div>
  );
}

function ChannelSummary({
  name,
  to,
  sent,
  figures,
}: {
  name: string;
  to: string;
  sent: number;
  figures: { label: string; value: number }[];
}) {
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
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
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          {figures.map((figure) => (
            <Figure key={figure.label} label={figure.label} value={figure.value} />
          ))}
        </dl>
      )}
    </Card>
  );
}

function Performance({ data }: { data: HubOverview }) {
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

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {whatsapp !== null ? (
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
        />
      ) : null}
      {email !== null ? (
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
        />
      ) : null}
    </div>
  );
}

function CostCard({ data }: { data: HubOverview }) {
  if (data.costs === null) return null;
  return (
    <Card className="flex flex-col gap-2 p-4 sm:p-6">
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

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Engage"
        title="Communication"
        description="Which channels are connected, and how WhatsApp and email are doing."
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
          <section aria-label="Channels" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {data.channels.map((channel) => (
              <ChannelCard key={channel.key} channel={channel} />
            ))}
          </section>
          <Performance data={data} />
          <CostCard data={data} />
        </>
      )}
    </div>
  );
}
