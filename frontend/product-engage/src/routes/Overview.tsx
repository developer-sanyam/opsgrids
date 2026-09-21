import { useCallback } from "react";
import type { ComponentType, SVGProps } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { ArrowRightIcon, GlobeIcon, PlugIcon, UsersIcon } from "@opsgrids/ui";

import { getConnection, listDomains, listSenders } from "@/api/client";
import type { ConnectionOut, DomainOut, SenderOut } from "@/api/types";
import { connectionKindLabel, formatNumber } from "@/lib/format";
import { Card, ErrorState, PageHeader, Skeleton } from "@/ui";

/**
 * Where an organization stands on the three things that have to be true before Engage sends
 * anything: a provider is connected, a domain is verified, and a sender has passed its test send.
 *
 * Each tile states the fact and links to the screen that changes it. It deliberately does not
 * *act*: "Connect a provider" from here would be a second entry point into a flow that lives on
 * one screen, and two ways in is two ways to leave it half-done.
 */

interface StepTileProps {
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  /** The screen this step happens on, named as the navigation names it. */
  title: string;
  to: string;
  /** The headline fact: "Google Workspace", "1 of 2 verified", "None tested yet". */
  headline: string;
  /** What that means, or what has to happen next. */
  detail: string;
  /** The link's own words: the action when the step is missing, the destination when it is done.
   *  "Open Connections" under "No provider connected" is a shrug where an instruction belongs. */
  cta: string;
  /** Whether the step is satisfied. The words above say so too — this only tints the marker. */
  done: boolean;
}

function StepTile({ icon: Icon, title, to, headline, detail, cta, done }: StepTileProps) {
  return (
    <Card className="flex flex-col gap-4 p-6">
      <div className="flex items-start gap-3">
        <span
          className={`grid size-11 shrink-0 place-items-center rounded-tile ${
            done ? "bg-positive-soft text-positive" : "bg-ground text-muted"
          }`}
        >
          <Icon className="size-5" />
        </span>
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-sm font-semibold text-muted">{title}</span>
          <span className="text-lg font-bold tracking-tight text-balance">{headline}</span>
        </div>
      </div>
      <p className="flex-1 text-sm text-muted">{detail}</p>
      <Link
        to={to}
        className="inline-flex h-9 w-fit items-center gap-1.5 rounded-full px-3 text-sm font-semibold text-accent-ink transition-colors hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {cta}
        <ArrowRightIcon className="size-4" />
      </Link>
    </Card>
  );
}

function connectionStep(connection: ConnectionOut | null): Omit<StepTileProps, "icon"> {
  if (connection === null) {
    return {
      title: "Connections",
      to: "/connection",
      headline: "No provider connected",
      detail:
        "Engage sends through your own provider, so nothing else can be set up until one is connected.",
      cta: "Connect a provider",
      done: false,
    };
  }
  return {
    title: "Connections",
    to: "/connection",
    headline: connectionKindLabel(connection.kind),
    detail:
      connection.status === "connected"
        ? `Sending as ${connection.username}, up to ${formatNumber(connection.daily_cap)} messages a day.`
        : `${connection.username} is disconnected. Mail will not go out until it is reconnected.`,
    cta: "Open Connections",
    done: connection.status === "connected",
  };
}

function domainStep(domains: DomainOut[]): Omit<StepTileProps, "icon"> {
  const verified = domains.filter((d) => d.status === "verified").length;
  if (domains.length === 0) {
    return {
      title: "Domains",
      to: "/domains",
      headline: "No sending domains",
      detail:
        "A domain proves the mail is yours. Add one and publish the DNS records it generates.",
      cta: "Add a domain",
      done: false,
    };
  }
  return {
    title: "Domains",
    to: "/domains",
    headline: `${formatNumber(verified)} of ${formatNumber(domains.length)} verified`,
    detail:
      verified === domains.length
        ? "Every domain's required records are published, so all of them can send."
        : "A domain sends once its required records resolve. The rest are waiting on DNS.",
    cta: verified === domains.length ? "Open Domains" : "Check the waiting domains",
    done: verified > 0,
  };
}

function senderStep(senders: SenderOut[]): Omit<StepTileProps, "icon"> {
  const usable = senders.filter((s) => s.usable).length;
  if (senders.length === 0) {
    return {
      title: "Senders",
      to: "/senders",
      headline: "No senders yet",
      detail: "A sender is the name and address a message arrives from — support@, or a person.",
      cta: "Add a sender",
      done: false,
    };
  }
  return {
    title: "Senders",
    to: "/senders",
    headline: `${formatNumber(usable)} of ${formatNumber(senders.length)} usable`,
    detail:
      usable === senders.length
        ? "Every sender has had a test message delivered, so a campaign can pick any of them."
        : "A sender becomes usable when a test message actually reaches the provider.",
    cta: usable === senders.length ? "Open Senders" : "Test the untested senders",
    done: usable > 0,
  };
}

function OverviewSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <Card key={i} className="flex flex-col gap-4 p-6">
          <div className="flex gap-3">
            <Skeleton className="size-11 rounded-tile" />
            <div className="flex flex-1 flex-col gap-2">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-5 w-36" />
            </div>
          </div>
          <Skeleton className="h-10 w-full rounded-tile" />
          <Skeleton className="h-9 w-32 rounded-full" />
        </Card>
      ))}
    </div>
  );
}

export function OverviewPage() {
  const loader = useCallback(
    () => Promise.all([getConnection(), listDomains(), listSenders()]),
    [],
  );
  const state = useAsync(loader, []);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Engage"
        title="Sending identity"
        description="Mail goes out as your domain and your people, not as OpsGrids. These three steps are what make that true."
      />

      {state.status === "loading" ? <OverviewSkeleton /> : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            message={state.error.message}
            title="Couldn't load your sending identity"
          />
        </Card>
      ) : null}

      {state.status === "success" ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {/* A page of at most one, so the first item is the connection and an empty page means
              there is none — the shape the service chose so this screen has no null to handle. */}
          <StepTile icon={PlugIcon} {...connectionStep(state.data[0].items[0] ?? null)} />
          <StepTile icon={GlobeIcon} {...domainStep(state.data[1].items)} />
          <StepTile icon={UsersIcon} {...senderStep(state.data[2].items)} />
        </div>
      ) : null}
    </div>
  );
}
