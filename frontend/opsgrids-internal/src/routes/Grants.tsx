import { useCallback, useId, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listLedger, listOrganizations, postOverride } from "@/api/client";
import type { CreditLedgerEntry, OrganizationRow, OverrideResult } from "@/api/types";
import { daysUntil, formatDate, relativeDays } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  EnvBadge,
  ErrorState,
  LedgerKindPill,
  Modal,
  Money,
  PageHeader,
  Pill,
  SearchInput,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { CheckCircleIcon, GiftIcon, PlusIcon } from "@/ui/icons";

/** Whether a ledger entry's validity window covers today. */
function isActiveNow(entry: CreditLedgerEntry): boolean {
  const fromPassed = (daysUntil(entry.valid_from) ?? 0) <= 0;
  const notExpired = entry.valid_until === null || (daysUntil(entry.valid_until) ?? 1) >= 0;
  return fromPassed && notExpired;
}

const INPUT =
  "h-11 rounded-tile bg-ground px-3 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";

/* ------------------------------- grant modal ------------------------------ */

function GrantModal({
  organizations,
  onClose,
  onDone,
}: {
  organizations: OrganizationRow[];
  onClose: () => void;
  onDone: (r: OverrideResult) => void;
}) {
  const orgId = useId();
  const amountId = useId();
  const expiryId = useId();
  const reasonId = useId();
  const [organization, setOrganization] = useState(() => organizations[0]?.organization_id ?? "");
  const [amount, setAmount] = useState("");
  const [isComp, setIsComp] = useState(true);
  const [expiry, setExpiry] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OverrideResult | null>(null);

  const amountMinor = Math.round(Number(amount) * 100);
  const selectedOrg = organizations.find((o) => o.organization_id === organization);
  const currency = selectedOrg?.currency ?? "INR";
  const valid = organization !== "" && amountMinor > 0 && reason.trim().length > 0;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setError(null);
    setSubmitting(true);
    try {
      const res = await postOverride({
        kind: "comp_grant",
        organization_id: organization,
        amount_minor: amountMinor,
        currency,
        is_comp: isComp,
        reason: reason.trim(),
        valid_until: expiry ? `${expiry}T23:59:59Z` : null,
      });
      setResult(res);
      onDone(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The grant was rejected.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Grant credit or comp access"
      description="Writes one append-only credit-ledger entry. Balance stays a SUM; nothing is mutated."
      icon={<GiftIcon className="size-5" />}
    >
      {result ? (
        <div className="flex flex-col items-center gap-4 py-4 text-center">
          <span className="grid size-14 place-items-center rounded-full bg-active-soft text-active">
            <CheckCircleIcon className="size-7" />
          </span>
          <p className="text-base font-bold">Grant recorded</p>
          <p className="text-sm text-muted">{result.audit.summary}</p>
          <Button onClick={onClose} className="w-full">
            Done
          </Button>
        </div>
      ) : (
        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          {error ? (
            <p role="alert" className="rounded-tile bg-suspended-soft px-3 py-2.5 text-sm font-medium text-suspended">
              {error}
            </p>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <label htmlFor={orgId} className="text-sm font-semibold text-ink-soft">
              Customer
            </label>
            <select id={orgId} value={organization} onChange={(e) => setOrganization(e.target.value)} className={`${INPUT} cursor-pointer`}>
              {organizations.map((o) => (
                <option key={o.organization_id} value={o.organization_id}>
                  {o.name}
                </option>
              ))}
            </select>
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="pb-1 text-sm font-semibold text-ink-soft">Grant type</legend>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                aria-pressed={isComp}
                onClick={() => setIsComp(true)}
                className={`rounded-tile border px-3 py-2.5 text-left text-sm font-semibold transition-colors ${
                  isComp ? "border-accent bg-accent-soft text-accent-ink" : "border-line text-muted hover:bg-ground"
                }`}
              >
                Comp access
              </button>
              <button
                type="button"
                aria-pressed={!isComp}
                onClick={() => setIsComp(false)}
                className={`rounded-tile border px-3 py-2.5 text-left text-sm font-semibold transition-colors ${
                  !isComp ? "border-accent bg-accent-soft text-accent-ink" : "border-line text-muted hover:bg-ground"
                }`}
              >
                Monetary credit
              </button>
            </div>
          </fieldset>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <label htmlFor={amountId} className="text-sm font-semibold text-ink-soft">
                Amount (₹)
              </label>
              <input id={amountId} type="number" min="1" step="1" required inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="45000" className={`${INPUT} tnum`} />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor={expiryId} className="text-sm font-semibold text-ink-soft">
                Expires (optional)
              </label>
              <input id={expiryId} type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} className={`${INPUT} tnum`} />
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={reasonId} className="text-sm font-semibold text-ink-soft">
              Reason (required)
            </label>
            <input id={reasonId} required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. 3 months complimentary — migration goodwill" className={INPUT} />
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !valid}>
              {submitting ? "Recording…" : "Record grant"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/* ------------------------------ reverse modal ----------------------------- */

function ReverseModal({
  entry,
  onClose,
  onDone,
}: {
  entry: CreditLedgerEntry;
  onClose: () => void;
  onDone: (r: OverrideResult) => void;
}) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (reason.trim().length === 0) return;
    setError(null);
    setSubmitting(true);
    try {
      const res = await postOverride({
        kind: "comp_grant",
        organization_id: entry.organization_id,
        amount_minor: -entry.amount_minor,
        currency: entry.currency,
        is_comp: entry.is_comp,
        reason: `Reversal of ${entry.entry_id} — ${reason.trim()}`,
        valid_until: null,
      });
      onDone(res);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The reversal was rejected.");
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Reverse grant"
      description="Writes a reversing entry. The original grant stays on the ledger, unchanged."
    >
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        {error ? (
          <p role="alert" className="rounded-tile bg-suspended-soft px-3 py-2.5 text-sm font-medium text-suspended">
            {error}
          </p>
        ) : null}
        <p className="text-sm text-muted">
          Reversing{" "}
          <Money minor={entry.amount_minor} currency={entry.currency} className="font-semibold text-ink" /> granted to{" "}
          <span className="font-semibold text-ink">{entry.organization_name}</span> — “{entry.reason}”.
        </p>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={reasonId} className="text-sm font-semibold text-ink-soft">
            Reason (required)
          </label>
          <input id={reasonId} required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Payment recovered" className={INPUT} />
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button variant="danger" type="submit" disabled={submitting || reason.trim().length === 0}>
            {submitting ? "Reversing…" : "Reverse grant"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/* --------------------------------- page ---------------------------------- */

const NO_ROWS: CreditLedgerEntry[] = [];

export function GrantsPage() {
  const [search, setSearch] = useState("");
  const [compOnly, setCompOnly] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [granting, setGranting] = useState(false);
  const [reversing, setReversing] = useState<CreditLedgerEntry | null>(null);
  const [banner, setBanner] = useState<OverrideResult | null>(null);

  const loader = useCallback(
    () => listLedger({ search, ...(compOnly ? { compOnly: true } : {}) }),
    [search, compOnly],
  );
  const ledger = useAsync(loader, [search, compOnly, reloadKey]);
  const orgs = useAsync(() => listOrganizations(), []);

  const rows = ledger.status === "success" ? ledger.data.items : NO_ROWS;
  const orgList = orgs.status === "success" ? orgs.data.items : [];

  const onComp = useMemo(
    () => rows.filter((e) => e.is_comp && e.kind === "grant" && isActiveNow(e)),
    [rows],
  );
  const compValue = onComp.reduce((sum, e) => sum + e.amount_minor, 0);
  const lapsingSoon = onComp.filter((e) => {
    const left = daysUntil(e.valid_until);
    return left !== null && left <= 30;
  }).length;

  function handleDone(res: OverrideResult) {
    setBanner(res);
    setReloadKey((k) => k + 1);
  }

  const columns: Column<CreditLedgerEntry>[] = [
    { key: "date", header: "Date", render: (e) => <span className="tnum text-muted">{formatDate(e.created_at)}</span> },
    {
      key: "org",
      header: "Organization",
      render: (e) => (
        <Link to={`/accounts/${e.organization_id}`} className="font-semibold text-ink hover:text-accent transition-colors">
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
          {e.is_comp ? <span className="rounded-full bg-comp-soft px-2 py-0.5 text-xs font-semibold text-comp">Comp</span> : null}
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
            <span className="text-pastdue">{relativeDays(e.valid_until)}</span>
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
        e.kind === "grant" && isActiveNow(e) ? (
          <button
            type="button"
            onClick={() => setReversing(e)}
            className="rounded-full px-2.5 h-8 text-xs font-semibold text-suspended hover:bg-suspended-soft transition-colors"
          >
            Reverse
          </button>
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
          <div className="flex items-center gap-2">
            <EnvBadge />
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setGranting(true)} disabled={orgs.status !== "success"}>
              Grant credit
            </Button>
          </div>
        }
      />

      {banner ? (
        <div role="status" className="flex items-center gap-3 rounded-card bg-active-soft px-4 py-3 text-sm text-active">
          <CheckCircleIcon className="size-5 shrink-0" />
          <span className="font-semibold">{banner.audit.summary}</span>
          <button type="button" onClick={() => setBanner(null)} className="ml-auto text-xs font-semibold text-active/70 hover:text-active">
            Dismiss
          </button>
        </div>
      ) : null}

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
          <Skeleton className="h-24 w-full" />
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
                  <Money minor={e.amount_minor} currency={e.currency} className="text-sm font-semibold text-comp" />
                  <span className="text-xs text-muted">{e.reason}</span>
                  <span className="mt-1 text-xs font-semibold text-pastdue">
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
          <div className="flex flex-col gap-2 py-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : ledger.status === "error" ? (
          <ErrorState message={ledger.error.message} />
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

      {granting ? <GrantModal organizations={orgList} onClose={() => setGranting(false)} onDone={handleDone} /> : null}
      {reversing ? <ReverseModal entry={reversing} onClose={() => setReversing(null)} onDone={handleDone} /> : null}
    </div>
  );
}
