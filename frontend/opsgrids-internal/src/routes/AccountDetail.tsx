import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { problemMessage, useAsync } from "@opsgrids/client";
import {
  ArrowLeftIcon,
  GiftIcon,
  LockIcon,
  PauseIcon,
  PlayIcon,
  RepeatIcon,
  TagIcon,
  useToast,
} from "@opsgrids/ui";

import { getCatalog, getOrganization } from "@/api/client";
import type { OverrideResult } from "@/api/types";
import { useOperatorAuth } from "@/auth/context";
import { daysUntil, formatDate, formatInt, relativeDays } from "@/lib/format";
import { referenceOf } from "@/lib/errors";
import {
  AccountStatusPill,
  Avatar,
  Button,
  Card,
  DefinitionList,
  InlineAlert,
  LoadFailure,
  Money,
  Skeleton,
  StatusPill,
} from "@/ui";
import type { Definition } from "@/ui";
import { InvoicesCard } from "./account/InvoicesCard";
import { LedgerCard } from "./account/LedgerCard";
import { SubscriptionsCard } from "./account/SubscriptionsCard";
import { UsageCard } from "./account/UsageCard";
import {
  BespokePriceDialog,
  CompGrantDialog,
  MigratePlanDialog,
  ReasonDialog,
  acceptedDescription,
} from "./overrides";

/** Which action dialog is open, or none. */
type ActionKind = null | "suspend" | "reactivate" | "comp" | "bespoke" | "migrate";

export function AccountDetailPage() {
  const { orgId = "" } = useParams();
  const { can } = useOperatorAuth();
  const toast = useToast();
  const [action, setAction] = useState<ActionKind>(null);

  const detail = useAsync(() => getOrganization(orgId), [orgId]);
  const catalog = useAsync(getCatalog, []);
  const catalogData = catalog.status === "success" ? catalog.data : null;
  const canOverride = can("overrides");

  function handleDone(result: OverrideResult) {
    toast.success("Override accepted.", { description: acceptedDescription(result) });
    detail.reload();
  }

  if (detail.status === "loading") {
    return (
      <div className="flex flex-col gap-5" aria-busy>
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-32 w-full rounded-tile" />
        <Skeleton className="h-64 w-full rounded-tile" />
      </div>
    );
  }

  if (detail.status === "error") {
    return (
      <div className="flex flex-col gap-4">
        <Link
          to="/accounts"
          className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted transition-colors hover:text-ink"
        >
          <ArrowLeftIcon className="size-4" />
          All accounts
        </Link>
        <Card className="p-6">
          <LoadFailure error={detail.error} title="Couldn't load this account" onRetry={detail.reload} />
        </Card>
      </div>
    );
  }

  const data = detail.data;
  const org = data.organization;
  const summary: Definition[] = [
    {
      term: "MRR",
      value:
        org.mrr_minor > 0 ? (
          <Money minor={org.mrr_minor} currency={org.currency} />
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    { term: "Seats", value: <span className="tnum">{formatInt(org.seats)}</span> },
    {
      term: "Comp / credit balance",
      value: <Money minor={data.comp_balance_minor} currency={org.currency} signed />,
    },
    { term: "Customer since", value: formatDate(org.created_at) },
    { term: "Last activity", value: relativeDays(org.last_activity_at) },
    {
      term: "Primary contact",
      value: (
        <a href={`mailto:${org.primary_contact.email}`} className="text-accent hover:text-accent-strong">
          {org.primary_contact.name}
        </a>
      ),
    },
  ];

  const trialLeft = org.status === "trial" ? daysUntil(org.trial_ends_at) : null;

  return (
    <div className="rise flex flex-col gap-5">
      <div className="flex flex-col gap-4">
        <Link
          to="/accounts"
          className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted transition-colors hover:text-ink"
        >
          <ArrowLeftIcon className="size-4" />
          All accounts
        </Link>

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-4">
            <Avatar name={org.name} size="lg" shape="square" />
            <div className="flex flex-col gap-1.5">
              <h1 className="display text-2xl font-extrabold leading-tight sm:text-3xl">{org.name}</h1>
              <div className="flex flex-wrap items-center gap-2">
                <span className="money text-sm text-faint">{org.slug}</span>
                <span className="text-faint">·</span>
                <span className="text-sm text-muted">{org.region}</span>
              </div>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <AccountStatusPill status={org.status} />
                {org.is_bespoke ? <StatusPill tone="accent">Bespoke pricing</StatusPill> : null}
                {org.locked ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-danger-soft px-2.5 py-1 text-xs font-semibold text-danger">
                    <LockIcon className="size-3.5" /> Access locked
                  </span>
                ) : null}
                {trialLeft !== null ? (
                  <span className="text-xs font-semibold text-late">
                    Trial ends {relativeDays(org.trial_ends_at)}
                  </span>
                ) : null}
              </div>
            </div>
          </div>

          {/* Operator actions — hidden from a role that cannot use them; the server refuses regardless. */}
          {canOverride ? (
            <div className="flex flex-wrap items-center gap-2">
              {org.status === "suspended" ? (
                <Button
                  variant="outline"
                  leadingIcon={<PlayIcon className="size-4" />}
                  onClick={() => setAction("reactivate")}
                >
                  Reactivate
                </Button>
              ) : (
                <Button
                  variant="danger"
                  leadingIcon={<PauseIcon className="size-4" />}
                  onClick={() => setAction("suspend")}
                >
                  Suspend
                </Button>
              )}
              <Button
                variant="outline"
                leadingIcon={<GiftIcon className="size-4" />}
                onClick={() => setAction("comp")}
              >
                Grant credit
              </Button>
              <Button
                variant="outline"
                leadingIcon={<TagIcon className="size-4" />}
                disabled={catalogData === null}
                onClick={() => setAction("bespoke")}
              >
                Bespoke price
              </Button>
              <Button
                variant="outline"
                leadingIcon={<RepeatIcon className="size-4" />}
                disabled={catalogData === null}
                onClick={() => setAction("migrate")}
              >
                Migrate plan
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted">Read-only — your operator role doesn't include account changes.</p>
          )}
        </div>
      </div>

      {canOverride && catalog.status === "error" ? (
        <InlineAlert
          tone="warning"
          title="Bespoke price and Migrate plan are unavailable — the plan catalog couldn't be loaded."
          reference={referenceOf(catalog.error)}
          action={
            <Button size="sm" variant="outline" onClick={catalog.reload}>
              Try again
            </Button>
          }
        >
          {problemMessage(catalog.error)}
        </InlineAlert>
      ) : null}

      <Card className="p-6">
        <DefinitionList items={summary} className="sm:grid-cols-3" />
      </Card>

      <SubscriptionsCard detail={data} />

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <UsageCard usage={data.usage} />
        <InvoicesCard invoices={data.invoices} />
      </div>

      <LedgerCard detail={data} />

      {/* Action dialogs — mounted only while open, so closing one resets it. */}
      {action === "suspend" || action === "reactivate" ? (
        <ReasonDialog kind={action} org={org} onClose={() => setAction(null)} onDone={handleDone} />
      ) : null}
      {action === "comp" ? (
        <CompGrantDialog org={org} organizations={[]} onClose={() => setAction(null)} onDone={handleDone} />
      ) : null}
      {action === "bespoke" && catalogData ? (
        <BespokePriceDialog
          org={org}
          organizations={[]}
          catalog={catalogData}
          onClose={() => setAction(null)}
          onDone={handleDone}
        />
      ) : null}
      {action === "migrate" && catalogData ? (
        <MigratePlanDialog
          org={org}
          subscriptions={data.subscriptions}
          catalog={catalogData}
          onClose={() => setAction(null)}
          onDone={handleDone}
        />
      ) : null}
    </div>
  );
}
