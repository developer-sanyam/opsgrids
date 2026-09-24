import { useCallback } from "react";
import type { ComponentType, SVGProps } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  CircleDotIcon,
  GlobeIcon,
  MailIcon,
  PlugIcon,
  UserIcon,
  UsersIcon,
} from "@opsgrids/ui";

import { getSetup } from "@/api/client";
import type { SetupStepName, SetupStepOut, SetupStepStatus } from "@/api/types";
import { Card, LoadFailure, PageHeader, Skeleton } from "@/ui";

/**
 * The "get started" checklist: five steps, in the order an organization actually completes
 * them, each folded server-side from the same rows every other Engage screen reads
 * (`GET /engage/setup`). Nothing here decides what "done" means — the service does, so this
 * screen can never disagree with the gate a campaign send actually runs into.
 *
 * Each tile states the fact and links to the screen that changes it. It deliberately does not
 * *act*: "Connect a provider" from here would be a second entry point into a flow that lives on
 * one screen, and two ways in is two ways to leave it half-done.
 */

const STEP_ICON: Record<SetupStepName, ComponentType<SVGProps<SVGSVGElement>>> = {
  connection: PlugIcon,
  domain: GlobeIcon,
  sender: UsersIcon,
  contacts: UserIcon,
  campaign: MailIcon,
};

const STEP_TITLE: Record<SetupStepName, string> = {
  connection: "Connections",
  domain: "Domains",
  sender: "Senders",
  contacts: "Contacts",
  campaign: "Campaigns",
};

/** The link's own words: the action when the step is missing, the destination when it is done,
 *  and what a blocked step needs first — "See what's blocking it" is a shrug where an
 *  instruction belongs, so blocked still points at the screen the fix happens on. */
function ctaFor(status: SetupStepStatus, title: string): string {
  if (status === "done") return `Open ${title}`;
  if (status === "blocked") return `Go to ${title}`;
  return `Set up ${title}`;
}

function StepMarker({ status }: { status: SetupStepStatus }) {
  if (status === "done") {
    return (
      <span className="grid size-11 shrink-0 place-items-center rounded-tile bg-positive-soft text-positive">
        <CheckCircleIcon className="size-5" />
      </span>
    );
  }
  return (
    <span className="grid size-11 shrink-0 place-items-center rounded-tile bg-ground text-muted">
      <CircleDotIcon className="size-5" />
    </span>
  );
}

function StepTile({ step }: { step: SetupStepOut }) {
  const Icon = STEP_ICON[step.step];
  const title = STEP_TITLE[step.step];
  return (
    <Card className="flex flex-col gap-4 p-6">
      <div className="flex items-start gap-3">
        <StepMarker status={step.status} />
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-muted">
            <Icon className="size-4" />
            {title}
          </span>
          {step.status === "blocked" ? (
            <span className="text-xs font-semibold uppercase tracking-wide text-late">
              Blocked
            </span>
          ) : null}
        </div>
      </div>
      <p className="flex-1 text-sm text-ink-soft">{step.detail}</p>
      <Link
        to={step.link}
        className="inline-flex h-9 w-fit items-center gap-1.5 rounded-full px-3 text-sm font-semibold text-accent-ink transition-colors hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {ctaFor(step.status, title)}
        <ArrowRightIcon className="size-4" />
      </Link>
    </Card>
  );
}

function OverviewSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 5 }).map((_, i) => (
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
  const loader = useCallback(() => getSetup(), []);
  const state = useAsync(loader, []);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Engage"
        title="Get started"
        description="Five steps stand between a fresh organization and its first campaign. Each links to the screen that moves it forward."
      />

      {state.status === "loading" ? <OverviewSkeleton /> : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your setup checklist" />
      ) : null}

      {state.status === "success" ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {state.data.steps.map((step) => (
            <StepTile key={step.step} step={step} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
