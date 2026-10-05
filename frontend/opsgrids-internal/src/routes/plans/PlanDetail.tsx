import { useCallback, useEffect, useState } from "react";

import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  InlineAlert,
  Skeleton,
  StatusBadge,
  Switch,
  Tabs,
} from "@opsgrids/ui";

import {
  deleteDraftVersion,
  getPlan,
  getVersion,
  publishVersion,
  reactivatePlan,
  retirePlan,
  withdrawVersion,
} from "@/api/catalog";
import type { Audience, PlanDetailOut, PlanVersionOut, VersionSummaryOut } from "@/api/catalog";
import { formatDate } from "@/lib/format";
import { productLabel } from "@/lib/labels";
import { hasZeroAmount } from "./draftSheet";
import { DraftVersionDialog } from "./DraftVersionDialog";
import { ReasonDialog } from "./ReasonDialog";
import { INTERVAL_LABEL } from "./planFormat";
import { VersionSheet } from "./VersionSheet";
import { VersionStateBadge } from "./VersionStateBadge";

const AUDIENCE_TABS = [
  { id: "public", label: "Public" },
  { id: "restricted", label: "Bespoke & partner" },
  { id: "all", label: "All" },
];

type Dialog =
  | { kind: "draft"; source: PlanVersionOut | null }
  | { kind: "publish"; version: PlanVersionOut }
  | { kind: "withdraw"; version: PlanVersionOut }
  | { kind: "delete"; version: PlanVersionOut }
  | { kind: "retire" }
  | { kind: "reactivate" };

type SheetState = { status: "loading" } | { status: "ready"; version: PlanVersionOut } | { status: "error"; message: string };

interface PlanDetailProps {
  planId: string;
  canWrite: boolean;
  onBack: () => void;
  /** Announces a completed write in the page's live region. */
  onResult: (message: string) => void;
}

function audienceLabel(orgId: string | null): string {
  return orgId === null ? "Public" : `Bespoke · ${orgId.slice(0, 8)}…`;
}

/**
 * One plan: its header, a page of version summaries for the chosen audience, and — opened one at a
 * time — each version's full sheet with the actions its state allows. Dialogs live at this level,
 * outside anything a reload swaps out, so a refresh behind an open dialog never unmounts it.
 */
export function PlanDetail({ planId, canWrite, onBack, onResult }: PlanDetailProps) {
  const [audience, setAudience] = useState<Audience>("public");
  const [plan, setPlan] = useState<PlanDetailOut | null>(null);
  const [versions, setVersions] = useState<VersionSummaryOut[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [open, setOpen] = useState<Record<string, SheetState>>({});
  const [dialog, setDialog] = useState<Dialog | null>(null);

  useEffect(() => {
    let active = true;
    setLoadError(null);
    getPlan(planId, { audience })
      .then((detail) => {
        if (!active) return;
        setPlan(detail);
        setVersions(detail.versions);
        setNextCursor(detail.next_cursor);
      })
      .catch((err: unknown) => {
        if (active) setLoadError(err instanceof Error ? err.message : "The plan could not be loaded.");
      });
    return () => {
      active = false;
    };
  }, [planId, audience, reloadToken]);

  const loadSheet = useCallback((versionId: string) => {
    setOpen((current) => ({ ...current, [versionId]: { status: "loading" } }));
    getVersion(versionId)
      .then((version) => setOpen((current) => ({ ...current, [versionId]: { status: "ready", version } })))
      .catch((err: unknown) =>
        setOpen((current) => ({
          ...current,
          [versionId]: { status: "error", message: err instanceof Error ? err.message : "Could not load it." },
        })),
      );
  }, []);

  function toggle(versionId: string) {
    if (open[versionId]) {
      setOpen((current) => {
        const next = { ...current };
        delete next[versionId];
        return next;
      });
      return;
    }
    loadSheet(versionId);
  }

  async function loadMore() {
    if (nextCursor === null) return;
    setLoadingMore(true);
    try {
      const page = await getPlan(planId, { audience, cursor: nextCursor });
      setVersions((current) => [...current, ...page.versions]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "More versions could not be loaded.");
    } finally {
      setLoadingMore(false);
    }
  }

  function finished(message: string, versionId?: string) {
    setDialog(null);
    onResult(message);
    if (versionId !== undefined) loadSheet(versionId);
    setReloadToken((token) => token + 1);
  }

  if (loadError !== null && plan === null) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <Button variant="ghost" size="sm" onClick={onBack}>
            ← All plans
          </Button>
        </div>
        <ErrorState title="This plan could not be loaded" message={loadError} onRetry={() => setReloadToken((t) => t + 1)} />
      </div>
    );
  }

  if (plan === null) {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  const newest = versions[0];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Button variant="ghost" size="sm" onClick={onBack}>
          ← All plans
        </Button>
      </div>

      <Card className="flex flex-col gap-4 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-lg font-bold text-ink">{plan.name}</h2>
            <p className="text-sm text-muted">
              {productLabel(plan.product_key)} · <span className="mono">{plan.code}</span>
            </p>
          </div>
          <StatusBadge status={plan.state} />
        </div>
        <p className="text-sm text-muted">
          {plan.counts.published} published · {plan.counts.draft} draft · {plan.counts.grandfathered} withdrawn ·{" "}
          {plan.counts.bespoke} bespoke
        </p>
        {canWrite ? (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              onClick={() => {
                const sheet = newest ? open[newest.id] : undefined;
                if (newest && sheet?.status !== "ready") {
                  getVersion(newest.id)
                    .then((source) => setDialog({ kind: "draft", source }))
                    .catch(() => setDialog({ kind: "draft", source: null }));
                  return;
                }
                setDialog({ kind: "draft", source: sheet?.status === "ready" ? sheet.version : null });
              }}
            >
              New draft
            </Button>
            {plan.state === "active" ? (
              <Button variant="secondary" onClick={() => setDialog({ kind: "retire" })}>
                Retire plan
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => setDialog({ kind: "reactivate" })}>
                Reactivate plan
              </Button>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted">Read-only: changing the catalogue needs the owner role.</p>
        )}
      </Card>

      <section aria-labelledby="versions-heading" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h3 id="versions-heading" className="text-base font-semibold text-ink">
            Versions
          </h3>
          <Tabs
            label="Audience"
            variant="segmented"
            items={AUDIENCE_TABS}
            value={audience}
            onChange={(id) => setAudience(id as Audience)}
          />
        </div>

        {loadError !== null ? <InlineAlert tone="danger" title={loadError} /> : null}

        {versions.length === 0 ? (
          <EmptyState
            title="No versions for this audience"
            description={
              canWrite
                ? "Draft a version to give this plan a price. Nothing is offered until a version is published."
                : "Nothing has been drafted for this audience yet."
            }
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {versions.map((summary) => {
              const sheet = open[summary.id];
              return (
                <li key={summary.id}>
                  <Card className="flex flex-col gap-4 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex flex-wrap items-center gap-3">
                        <span className="text-base font-bold text-ink">v{summary.version_no}</span>
                        <VersionStateBadge state={summary.state} />
                        <span className="text-sm text-muted">
                          {summary.currency} · {INTERVAL_LABEL[summary.billing_interval] ?? summary.billing_interval} ·{" "}
                          {audienceLabel(summary.restricted_to_org_id)}
                          {summary.published_at ? ` · published ${formatDate(summary.published_at)}` : ""}
                        </span>
                      </div>
                      <Button
                        variant="secondary"
                        size="sm"
                        aria-expanded={sheet !== undefined}
                        aria-controls={`sheet-${summary.id}`}
                        onClick={() => toggle(summary.id)}
                      >
                        {sheet ? "Hide sheet" : `Show sheet (${summary.price_count} ${summary.price_count === 1 ? "price" : "prices"})`}
                      </Button>
                    </div>
                    {sheet ? (
                      <div id={`sheet-${summary.id}`} className="flex flex-col gap-4 border-t border-line pt-4">
                        {sheet.status === "loading" ? <Skeleton className="h-24 w-full" /> : null}
                        {sheet.status === "error" ? (
                          <ErrorState title="The sheet could not be loaded" message={sheet.message} onRetry={() => loadSheet(summary.id)} />
                        ) : null}
                        {sheet.status === "ready" ? (
                          <>
                            <VersionSheet version={sheet.version} />
                            {canWrite ? (
                              <VersionActions version={sheet.version} onAction={setDialog} />
                            ) : null}
                          </>
                        ) : null}
                      </div>
                    ) : null}
                  </Card>
                </li>
              );
            })}
          </ul>
        )}

        {nextCursor !== null ? (
          <div>
            <Button variant="secondary" loading={loadingMore} onClick={() => void loadMore()}>
              Load more versions
            </Button>
          </div>
        ) : null}
      </section>

      {dialog?.kind === "draft" ? (
        <DraftVersionDialog
          planId={plan.id}
          planName={plan.name}
          source={dialog.source}
          onClose={() => setDialog(null)}
          onDone={(created) => finished(`Draft v${created.version_no} of ${plan.name} created.`, created.id)}
        />
      ) : null}
      {dialog?.kind === "publish" ? (
        <PublishDialog
          version={dialog.version}
          planName={plan.name}
          onClose={() => setDialog(null)}
          onDone={() => finished(`v${dialog.version.version_no} of ${plan.name} is published.`, dialog.version.id)}
        />
      ) : null}
      {dialog?.kind === "withdraw" ? (
        <ReasonDialog
          title={`Withdraw v${dialog.version.version_no}?`}
          description={`Customers already on v${dialog.version.version_no} keep it and keep being billed for it. Nobody new can choose it, and it cannot be put back on sale.`}
          warning={{ tone: "warning", title: "Withdrawing cannot be undone." }}
          submitLabel={`Withdraw v${dialog.version.version_no}`}
          pendingLabel="Withdrawing…"
          onClose={() => setDialog(null)}
          submit={(reason, key) => withdrawVersion(dialog.version.id, reason, key)}
          onDone={() => finished(`v${dialog.version.version_no} of ${plan.name} is withdrawn.`, dialog.version.id)}
        />
      ) : null}
      {dialog?.kind === "delete" ? (
        <ReasonDialog
          title={`Delete draft v${dialog.version.version_no}?`}
          description="The draft and its price sheet are removed. It was never offered, so no customer is affected."
          submitLabel="Delete draft"
          pendingLabel="Deleting…"
          onClose={() => setDialog(null)}
          submit={(reason, key) => deleteDraftVersion(dialog.version.id, reason, key)}
          onDone={() => {
            setOpen((current) => {
              const next = { ...current };
              delete next[dialog.version.id];
              return next;
            });
            finished(`Draft v${dialog.version.version_no} of ${plan.name} deleted.`);
          }}
        />
      ) : null}
      {dialog?.kind === "retire" ? (
        <ReasonDialog
          title={`Retire ${plan.name}?`}
          description="Customers can no longer choose this plan. Existing subscriptions keep their version and keep billing. You can reactivate it later."
          submitLabel="Retire plan"
          pendingLabel="Retiring…"
          onClose={() => setDialog(null)}
          submit={(reason, key) => retirePlan(plan.id, reason, key)}
          onDone={() => finished(`${plan.name} is retired.`)}
        />
      ) : null}
      {dialog?.kind === "reactivate" ? (
        <ReasonDialog
          title={`Reactivate ${plan.name}?`}
          description="Every published version of this plan is offered again, exactly as it was."
          submitLabel="Reactivate plan"
          pendingLabel="Reactivating…"
          onClose={() => setDialog(null)}
          submit={(reason, key) => reactivatePlan(plan.id, reason, key)}
          onDone={() => finished(`${plan.name} is active again.`)}
        />
      ) : null}
    </div>
  );
}

function VersionActions({ version, onAction }: { version: PlanVersionOut; onAction: (dialog: Dialog) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {version.state === "draft" ? (
        <>
          <Button variant="primary" size="sm" onClick={() => onAction({ kind: "publish", version })}>
            Publish v{version.version_no}
          </Button>
          <Button variant="danger" size="sm" onClick={() => onAction({ kind: "delete", version })}>
            Delete draft
          </Button>
        </>
      ) : null}
      {version.state === "published" ? (
        <Button variant="danger" size="sm" onClick={() => onAction({ kind: "withdraw", version })}>
          Withdraw v{version.version_no}
        </Button>
      ) : null}
      <Button variant="secondary" size="sm" onClick={() => onAction({ kind: "draft", source: version })}>
        New draft from v{version.version_no}
      </Button>
    </div>
  );
}

function PublishDialog({
  version,
  planName,
  onClose,
  onDone,
}: {
  version: PlanVersionOut;
  planName: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const zero = hasZeroAmount(version);
  const [allowZero, setAllowZero] = useState(false);
  return (
    <ReasonDialog
      title={`Publish v${version.version_no} of ${planName}?`}
      description={
        version.restricted_to_org_id === null
          ? "Every customer of this product can choose it as soon as it is published."
          : `Only organization ${version.restricted_to_org_id} can see and choose it.`
      }
      warning={{
        tone: "warning",
        title: "Publishing is permanent.",
        body: "A published version can never be edited or deleted. Its prices, features and seat classes are frozen for every subscription that pins it — a mistake can only be withdrawn and replaced by a new version.",
      }}
      submitLabel={`Publish v${version.version_no} permanently`}
      pendingLabel="Publishing…"
      onClose={onClose}
      submit={(reason, key) => publishVersion(version.id, { reason, allow_zero_amounts: zero && allowZero }, key)}
      onDone={onDone}
    >
      {zero ? (
        <Switch
          label="This version deliberately prices something at zero"
          checked={allowZero}
          onChange={setAllowZero}
        />
      ) : null}
    </ReasonDialog>
  );
}
