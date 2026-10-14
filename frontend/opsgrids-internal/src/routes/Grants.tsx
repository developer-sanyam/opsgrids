import { useMemo, useState } from "react";
import { Dialog, FormDialog, Select, TextField } from "@opsgrids/ui";
import { Link } from "react-router-dom";

import { listLedger, listOrganizations, postOverride } from "@/api/client";
import type { CreditLedgerEntryAdminOut, OverrideResult } from "@/api/types";
import { formatDate } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  EnvBadge,
  ErrorState,
  LedgerKindPill,
  Money,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { CheckCircleIcon, GiftIcon, PlusIcon } from "@/ui/icons";

/* ------------------------------- grant modal ------------------------------ */

/** `POST /admin/overrides` for `comp_grant` takes only an organization, an amount, a currency and a
 *  reason — no comp-vs-credit distinction and no expiry exist on the live ledger schema
 *  (`entry_type`/`valid_until` have no such flag), so this form doesn't offer either. */
function GrantModal({
  organizationIds,
  onClose,
  onDone,
}: {
  organizationIds: string[];
  onClose: () => void;
  onDone: (r: OverrideResult) => void;
}) {
  const [organization, setOrganization] = useState(() => organizationIds[0] ?? "");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [amountError, setAmountError] = useState<string | undefined>(undefined);
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OverrideResult | null>(null);

  const amountMinor = Math.round(Number(amount) * 100);

  async function handleSubmit() {
    const badAmount = amount === "" || !(amountMinor > 0) ? "Enter an amount greater than zero." : undefined;
    const badReason = reason.trim().length === 0 ? "Enter a reason." : undefined;
    setAmountError(badAmount);
    setReasonError(badReason);
    if (badAmount || badReason || organization === "" || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const res = await postOverride({
        action: "comp_grant",
        organization_id: organization,
        amount_minor: amountMinor,
        currency: "INR",
        reason: reason.trim(),
      });
      setResult(res);
      onDone(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The grant was rejected.");
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <Dialog open onClose={onClose} title="Grant credit" description="Writes one append-only credit-ledger entry.">
        <div className="flex flex-col items-center gap-4 py-4 text-center" role="status" aria-live="polite">
          <span className="grid size-14 place-items-center rounded-full bg-present-soft text-present">
            <CheckCircleIcon className="size-7" />
          </span>
          <p className="text-base font-bold">Grant recorded</p>
          <p className="text-sm text-muted">Delegated to {result.delegated_to}-service.</p>
          <Button onClick={onClose} className="w-full">
            Done
          </Button>
        </div>
      </Dialog>
    );
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void handleSubmit()}
      title="Grant credit"
      description="Writes one append-only credit-ledger entry."
      submitLabel="Record grant"
      pendingLabel="Recording…"
      pending={submitting}
      {...(error !== null ? { error } : {})}
    >
      <Select
        label="Organization id"
        value={organization}
        onChange={(e) => setOrganization(e.target.value)}
        options={organizationIds.map((id) => ({ value: id, label: id }))}
      />
      <TextField
        label="Amount (₹)"
        type="number"
        min="1"
        step="1"
        required
        inputMode="numeric"
        value={amount}
        error={amountError}
        onChange={(e) => setAmount(e.target.value)}
        placeholder="45000"
        className="tnum"
      />
      <TextField
        label="Reason (required)"
        required
        value={reason}
        error={reasonError}
        onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. 3 months complimentary — migration goodwill"
      />
    </FormDialog>
  );
}

/* --------------------------------- page ---------------------------------- */

const NO_ROWS: CreditLedgerEntryAdminOut[] = [];

export function GrantsPage() {
  const [search, setSearch] = useState("");
  const [entryType, setEntryType] = useState<string>("all");
  const [reloadKey, setReloadKey] = useState(0);
  const [granting, setGranting] = useState(false);
  const [banner, setBanner] = useState<OverrideResult | null>(null);

  const loader = () => listLedger({ size: 200 });
  const ledger = useAsync(loader, [reloadKey]);
  const orgs = useAsync(() => listOrganizations(), []);

  const all = useMemo(
    () => (ledger.status === "success" ? ledger.data.items : NO_ROWS),
    [ledger],
  );
  const entryTypes = useMemo(() => [...new Set(all.map((e) => e.entry_type))].sort(), [all]);
  const organizationIds = orgs.status === "success" ? orgs.data.organizations.map((o) => o.organization_id) : [];

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return all.filter((e) => {
      const matchQuery =
        q === "" || e.organization_id.toLowerCase().includes(q) || e.reason.toLowerCase().includes(q);
      const matchType = entryType === "all" || e.entry_type === entryType;
      return matchQuery && matchType;
    });
  }, [all, search, entryType]);

  function handleDone(res: OverrideResult) {
    setBanner(res);
    setReloadKey((k) => k + 1);
  }

  const columns: Column<CreditLedgerEntryAdminOut>[] = [
    { key: "date", header: "Date", render: (e) => <span className="tnum text-muted">{formatDate(e.created_at)}</span> },
    {
      key: "org",
      header: "Organization",
      mobile: "title",
      render: (e) => (
        <Link to={`/accounts/${e.organization_id}`} className="mono block truncate text-sm font-semibold text-ink hover:text-accent transition-colors max-md:py-3">
          {e.organization_id}
        </Link>
      ),
    },
    { key: "kind", header: "Entry", mobile: "status", render: (e) => <LedgerKindPill kind={e.entry_type} /> },
    {
      key: "reason",
      header: "Reason",
      cellClassName: "hidden lg:table-cell max-w-xs",
      headerClassName: "hidden lg:table-cell",
      render: (e) => <span className="text-muted">{e.reason}</span>,
    },
    {
      key: "until",
      header: "Valid until",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (e) => (e.valid_until ? <span className="tnum text-muted">{formatDate(e.valid_until)}</span> : <span className="text-faint">no expiry</span>),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      render: (e) => <Money minor={e.amount_minor} currency={e.currency} signed className="font-semibold" />,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Credit ledger"
        title="Grants & Credits"
        description="Every append-only credit-ledger entry across all tenants."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <EnvBadge />
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setGranting(true)} disabled={orgs.status !== "success"}>
              Grant credit
            </Button>
          </div>
        }
      />

      <p className="text-xs text-faint">
        The live ledger has no comp-vs-credit flag and no reversal action reachable from this
        console yet — a reversing entry (`entry_type: "reversal"`) can exist, but only a backend
        write path can create one today.
      </p>

      {banner ? (
        <div role="status" className="flex items-center gap-3 rounded-card bg-present-soft px-4 py-3 text-sm text-present">
          <CheckCircleIcon className="size-5 shrink-0" />
          <span className="font-semibold">Delegated to {banner.delegated_to}-service.</span>
          <button type="button" onClick={() => setBanner(null)} className="ml-auto text-xs font-semibold text-present/70 hover:text-present">
            Dismiss
          </button>
        </div>
      ) : null}

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search the ledger"
            placeholder="Search by organization id or reason…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div role="group" aria-label="Filter by entry type" className="-mx-1 -my-1 flex items-center gap-1.5 overflow-x-auto px-1 py-1">
            <Pill active={entryType === "all"} onClick={() => setEntryType("all")}>
              All entries
            </Pill>
            {entryTypes.map((t) => (
              <Pill key={t} active={entryType === t} onClick={() => setEntryType(t)}>
                {t}
              </Pill>
            ))}
          </div>
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
            rowKey={(e) => e.id}
            caption="Credit-ledger entries across all tenants"
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

      {granting ? <GrantModal organizationIds={organizationIds} onClose={() => setGranting(false)} onDone={handleDone} /> : null}
    </div>
  );
}
