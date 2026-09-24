import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { GiftIcon, PlusIcon, useToast } from "@opsgrids/ui";

import { listLedger, listOrganizations } from "@/api/client";
import type { CreditLedgerEntry, OverrideResult } from "@/api/types";
import { useOperatorAuth } from "@/auth/context";
import { daysUntil, formatDate, relativeDays } from "@/lib/format";
import {
  Button,
  Card,
  CompTag,
  DataTable,
  EmptyState,
  EnvBadge,
  LedgerKindPill,
  LoadFailure,
  Money,
  PageHeader,
  Pill,
  SearchInput,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { CompGrantDialog, ReverseDialog, acceptedDescription } from "./overrides";

/** Whether a ledger entry's validity window covers today. */
function isActiveNow(entry: CreditLedgerEntry): boolean {
  const fromPassed = (daysUntil(entry.valid_from) ?? 0) <= 0;
  const notExpired = entry.valid_until === null || (daysUntil(entry.valid_until) ?? 1) >= 0;
  return fromPassed && notExpired;
}

const NO_ROWS: CreditLedgerEntry[] = [];

export function GrantsPage() {
  const { can } = useOperatorAuth();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const [compOnly, setCompOnly] = useState(false);
  const [granting, setGranting] = useState(false);
  const [reversing, setReversing] = useState<CreditLedgerEntry | null>(null);

  const loader = useCallback(
    () => listLedger({ search, ...(compOnly ? { compOnly: true } : {}) }),
    [search, compOnly],
  );
  const ledger = useAsync(loader, [search, compOnly]);
  const orgs = useAsync(() => listOrganizations(), []);

  const rows = ledger.status === "success" ? ledger.data.items : NO_ROWS;
  const orgList = orgs.status === "success" ? orgs.data.items : [];
  const canOverride = can("overrides");

  const onComp = useMemo(
    () => rows.filter((e) => e.is_comp && e.kind === "grant" && isActiveNow(e)),
    [rows],
  );
  const compValue = onComp.reduce((sum, e) => sum + e.amount_minor, 0);
  const lapsingSoon = onComp.filter((e) => {
    const left = daysUntil(e.valid_until);
    return left !== null && left <= 30;
  }).length;

  function handleDone(title: string) {
    return (result: OverrideResult) => {
      toast.success(title, { description: acceptedDescription(result) });
      ledger.reload();
    };
  }

  const columns: Column<CreditLedgerEntry>[] = [
    { key: "date", header: "Date", render: (e) => <span className="tnum text-muted">{formatDate(e.created_at)}</span> },
    {
      key: "org",
      header: "Organization",
      render: (e) => (
        <Link
          to={`/accounts/${e.organization_id}`}
          className="font-semibold text-ink transition-colors hover:text-accent"
        >
          {e.organization_name}
        </Link>
      ),
    },
    {
      key: "kind",
      header: "Entry",
      render: (e) => (
        <div className="flex items-center gap-2">
          <LedgerKindPill kind={e.kind} />
          {e.is_comp ? <CompTag /> : null}
        </div>
      ),
    },
    {
      key: "reason",
      header: "Reason",
      cellClassName: "hidden lg:table-cell max-w-xs",
      headerClassName: "hidden lg:table-cell",
      render: (e) => <span className="text-muted">{e.reason}</span>,
    },
    {
      key: "until",
      header: "Lapses",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (e) =>
        e.valid_until ? (
          isActiveNow(e) ? (
            <span className="text-late">{relativeDays(e.valid_until)}</span>
          ) : (
            <span className="text-faint">{formatDate(e.valid_until)}</span>
          )
        ) : (
          <span className="text-faint">no expiry</span>
        ),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      render: (e) => <Money minor={e.amount_minor} currency={e.currency} signed className="font-semibold" />,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (e) =>
        canOverride && e.kind === "grant" && isActiveNow(e) ? (
          <Button size="sm" variant="ghost" onClick={() => setReversing(e)} aria-label={`Reverse grant to ${e.organization_name}`}>
            Reverse
          </Button>
        ) : null,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Credit ledger"
        title="Grants & Credits"
        description="Who is on comp access and when it lapses — a query over append-only ledger entries."
        actions={
          <>
            <EnvBadge />
            {canOverride ? (
              <Button
                leadingIcon={<PlusIcon className="size-4" />}
                onClick={() => setGranting(true)}
                disabled={orgs.status !== "success"}
              >
                Grant credit
              </Button>
            ) : null}
          </>
        }
      />

      {/* On comp now */}
      <Card className="flex flex-col gap-5 p-6">
        <SectionHeader
          title="On comp access now"
          subtitle="Complimentary grants whose window covers today"
          action={
            <div className="flex items-center gap-5">
              <div className="text-right">
                <span className="block text-xs font-semibold uppercase tracking-wide text-faint">Active value</span>
                <Money minor={compValue} currency="INR" className="text-lg font-bold" />
              </div>
              <div className="text-right">
                <span className="block text-xs font-semibold uppercase tracking-wide text-faint">Lapsing ≤ 30d</span>
                <span className="tnum text-lg font-bold">{lapsingSoon}</span>
              </div>
            </div>
          }
        />
        {ledger.status === "loading" ? (
          <Skeleton className="h-24 w-full rounded-tile" />
        ) : ledger.status === "error" ? (
          <LoadFailure error={ledger.error} title="Couldn't load the credit ledger" onRetry={ledger.reload} />
        ) : onComp.length === 0 ? (
          <p className="py-2 text-sm text-muted">No account is on comp access right now.</p>
        ) : (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {onComp.map((e) => (
              <li key={e.entry_id}>
                <Link
                  to={`/accounts/${e.organization_id}`}
                  className="flex flex-col gap-1 rounded-tile border border-line bg-surface p-4 transition-colors hover:bg-ground"
                >
                  <span className="font-bold text-ink">{e.organization_name}</span>
                  <Money minor={e.amount_minor} currency={e.currency} className="text-sm font-semibold text-cyan" />
                  <span className="text-xs text-muted">{e.reason}</span>
                  <span className="mt-1 text-xs font-semibold text-late">
                    {e.valid_until ? `Lapses ${relativeDays(e.valid_until)}` : "No expiry"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Full ledger */}
      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search the ledger"
            placeholder="Search by organization or reason…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <Pill active={compOnly} leadingIcon={<GiftIcon className="size-4" />} onClick={() => setCompOnly((v) => !v)}>
            Comp entries only
          </Pill>
        </div>

        {ledger.status === "loading" ? (
          <div className="flex flex-col gap-2 py-2" aria-busy>
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : ledger.status === "error" ? (
          <LoadFailure error={ledger.error} title="Couldn't load the credit ledger" onRetry={ledger.reload} />
        ) : (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(e) => e.entry_id}
            caption="Credit-ledger entries across all tenants"
            dense
            emptyState={
              <EmptyState
                icon={<GiftIcon className="size-6" />}
                title="No ledger entries"
                description="No grant, consumption or reversal matches these filters."
              />
            }
          />
        )}
      </Card>

      {granting ? (
        <CompGrantDialog
          org={null}
          organizations={orgList}
          onClose={() => setGranting(false)}
          onDone={handleDone("Grant recorded.")}
        />
      ) : null}
      {reversing ? (
        <ReverseDialog entry={reversing} onClose={() => setReversing(null)} onDone={handleDone("Grant reversed.")} />
      ) : null}
    </div>
  );
}
