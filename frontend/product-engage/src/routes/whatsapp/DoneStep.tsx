import { useCallback } from "react";
import type { Ref } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  CheckIcon,
  ChevronRight,
  FileTextIcon,
  InboxIcon,
  ShieldIcon,
  SparkIcon,
  cn,
} from "@opsgrids/ui";
import type { ComponentType, SVGProps } from "react";

import { getWhatsAppHealth, listWhatsAppNumbers } from "@/api/whatsappSignup";
import type { WhatsAppHealth, WhatsAppNumber } from "@/api/whatsappSignup";
import { formatNumber } from "@/lib/format";
import { Card, InlineAlert, Skeleton } from "@/ui";
import { displayNameLabel, limitFromString, limitLabel, maskedId, qualityFromString, qualityLabel } from "./labels";
import { LinkButton, LoadError } from "./parts";
import { TestMessageForm } from "./TestMessageForm";
import { useWabaId } from "./useWabaId";

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

interface NextStep {
  to: string;
  title: string;
  text: string;
  icon: Icon;
  tile: string;
}

function nextSteps(templateCount: number): NextStep[] {
  return [
    {
      to: "/automations",
      title: "Create your first automation",
      text: "Start from a recipe, such as an order confirmation.",
      icon: SparkIcon,
      tile: "bg-positive-soft text-positive",
    },
    {
      to: "/whatsapp/templates",
      title: `Review your ${formatNumber(templateCount)} templates`,
      text: "Check categories and variables before you use them.",
      icon: FileTextIcon,
      tile: "bg-accent-soft text-accent-ink",
    },
    {
      to: "/inbox",
      title: "Open the shared inbox",
      text: "Answer customers with their CRM and order history beside you.",
      icon: InboxIcon,
      tile: "bg-info-soft text-info",
    },
    {
      to: "/contacts",
      title: "Add an opt-in source",
      text: "Contacts need consent before templates can be sent to them.",
      icon: ShieldIcon,
      tile: "bg-late-soft text-late",
    },
  ];
}

interface FactData {
  label: string;
  value: string;
  good?: boolean;
}

function Fact({ label, value, good = false, wide }: FactData & { wide: boolean }) {
  return (
    <div className={cn("flex flex-col gap-1 bg-surface p-4", wide && "sm:col-span-2")}>
      <dt className="text-caption font-semibold text-muted">{label}</dt>
      <dd className={cn("m-0 text-body font-bold", good ? "text-positive" : "text-ink")}>{value}</dd>
    </div>
  );
}

function DoneSkeleton() {
  return (
    <Card className="flex flex-col gap-6 p-6 sm:p-8" aria-hidden>
      <Skeleton className="h-14 w-2/3" />
      <Skeleton className="h-40 w-full rounded-card" />
      <Skeleton className="h-48 w-full rounded-card" />
    </Card>
  );
}

function Summary({
  health,
  number,
  wabaId,
  headingRef,
}: {
  health: WhatsAppHealth;
  number: WhatsAppNumber | null;
  wabaId: string | null;
  headingRef: Ref<HTMLHeadingElement>;
}) {
  const quality = number?.quality_rating ?? qualityFromString(health.number?.quality_rating ?? "");
  const limit = number?.messaging_limit ?? limitFromString(health.number?.messaging_limit ?? "");
  const subtitle = [number?.verified_name, number?.display_phone_number].filter(Boolean).join(" · ");
  const facts: FactData[] = [
    ...(wabaId !== null ? [{ label: "WhatsApp Business Account", value: maskedId(wabaId) }] : []),
    ...(number !== null
      ? [{ label: "Display name", value: displayNameLabel(number), good: number.name_status === "approved" }]
      : []),
    { label: "Quality rating", value: qualityLabel(quality), good: quality === "green" },
    { label: "Messaging limit", value: limitLabel(limit) },
    { label: "Templates", value: `${formatNumber(health.templates.count)} synced from Meta` },
  ];
  return (
    <Card className="flex min-w-0 flex-col gap-6 p-6 sm:p-8 lg:col-span-2">
      <div className="flex items-center gap-4">
        <span aria-hidden className="grid size-14 shrink-0 place-items-center rounded-full bg-positive-soft text-positive">
          <CheckIcon className="size-7" strokeWidth={2.6} />
        </span>
        <div className="flex flex-col gap-1">
          <h1 ref={headingRef} tabIndex={-1} className="text-page-sm text-balance outline-none sm:text-page">
            WhatsApp is connected
          </h1>
          {subtitle !== "" ? <span className="text-body text-muted">{subtitle}</span> : null}
        </div>
      </div>

      <dl className="m-0 grid gap-px overflow-hidden rounded-card bg-line shadow-card sm:grid-cols-2">
        {facts.map((fact, index) => (
          <Fact key={fact.label} {...fact} wide={facts.length % 2 === 1 && index === facts.length - 1} />
        ))}
      </dl>

      <TestMessageForm />
    </Card>
  );
}

/**
 * Step 4: the connection as the server now sees it — read back, not assumed. Everything on it comes
 * from `health` and `numbers`; if the server says it is not connected, so does this screen, rather
 * than congratulating a customer whose setup did not land.
 */
export function DoneStep({ headingRef }: { headingRef: Ref<HTMLHeadingElement> }) {
  const loader = useCallback(() => Promise.all([getWhatsAppHealth(), listWhatsAppNumbers()]), []);
  const state = useAsync(loader, []);
  const wabaId = useWabaId();

  if (state.status === "loading") return <DoneSkeleton />;
  if (state.status === "error") {
    return <LoadError error={state.error} title="Couldn't check the connection" onRetry={state.reload} />;
  }

  const [health, numbers] = state.data;
  if (!health.connected) {
    return (
      <Card className="mx-auto flex w-full max-w-2xl flex-col gap-4 p-6 sm:p-8">
        <h1 ref={headingRef} tabIndex={-1} className="text-page-sm text-balance outline-none sm:text-page">
          WhatsApp isn&apos;t connected yet
        </h1>
        <InlineAlert tone="warning" live="off" title="The server doesn't show a connection">
          Setup may still be running, or it may not have finished. Check its status, or start again.
        </InlineAlert>
        <div>
          <LinkButton to="/whatsapp/connect" variant="primary">
            Back to setup
          </LinkButton>
        </div>
      </Card>
    );
  }

  const number = numbers.items.find((n) => n.is_default) ?? numbers.items[0] ?? null;
  return (
    <div className="mx-auto grid w-full max-w-5xl items-start gap-6 lg:grid-cols-3">
      <Summary health={health} number={number} wabaId={wabaId} headingRef={headingRef} />
      <aside aria-label="Next steps" className="flex flex-col gap-3">
        <h2 className="text-card-title text-ink">Next steps</h2>
        {nextSteps(health.templates.count).map(({ to, title, text, icon: StepIcon, tile }) => (
          <Link
            key={to}
            to={to}
            className="flex items-center gap-3 rounded-card bg-surface p-4 text-ink shadow-card transition-colors hover:bg-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <span aria-hidden className={cn("grid size-10 shrink-0 place-items-center rounded-tile", tile)}>
              <StepIcon className="size-5" />
            </span>
            <span className="flex min-w-0 flex-1 flex-col gap-1">
              <span className="text-body font-bold">{title}</span>
              <span className="text-caption text-muted">{text}</span>
            </span>
            <ChevronRight aria-hidden className="size-4 shrink-0 text-muted" />
          </Link>
        ))}
        <LinkButton to="/whatsapp">Go to WhatsApp overview</LinkButton>
      </aside>
    </div>
  );
}
