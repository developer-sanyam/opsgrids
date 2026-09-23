import { useState } from "react";

import { ApiError, useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  InlineAlert,
  SectionHeader,
  Skeleton,
  Tag,
  TextField,
} from "@opsgrids/ui";
import type { TagTone } from "@opsgrids/ui";

import { checkDomain, claimDomain, getDomain, releaseDomain } from "./domain";
import type { CheckState, DnsRecord, DomainOut, DomainStatus } from "./domain";

const STATUS: Record<DomainStatus, { label: string; tone: TagTone }> = {
  unverified: { label: "Waiting for DNS", tone: "neutral" },
  verified: { label: "Verified — certificate pending", tone: "info" },
  live: { label: "Live", tone: "positive" },
  failing: { label: "Failing", tone: "negative" },
  suspended: { label: "Suspended", tone: "warning" },
};

const CHECK_LABEL: Record<keyof NonNullable<DomainOut["checks"]>, string> = {
  ownership: "Ownership (TXT record)",
  routing: "Routing (points at OpsGrids)",
  certificate: "HTTPS certificate",
};

const CHECK_STATE: Record<CheckState["state"], { label: string; tone: TagTone }> = {
  pending: { label: "Pending", tone: "neutral" },
  ok: { label: "OK", tone: "positive" },
  failing: { label: "Failing", tone: "negative" },
};

function formatTime(iso: string | null): string | null {
  if (iso === null) return null;
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** What a failed claim says, mapped from the service's error codes (ADR-0012 §11). */
function claimErrorMessage(error: unknown): { field: string | null; banner: string | null } {
  if (!(error instanceof ApiError)) {
    return { field: null, banner: "Couldn't reach the server. Nothing changed — try again." };
  }
  const code = error.problem?.errors?.[0]?.code;
  if (code === "custom_domain_taken") {
    return { field: "Another store already uses this domain.", banner: null };
  }
  if (code?.startsWith("custom_domain_") === true) {
    return { field: error.problem?.errors?.[0]?.message ?? "That isn't a domain we can use.", banner: null };
  }
  return { field: null, banner: error.problem?.detail ?? error.message };
}

function RecordsTable({ caption, records }: { caption: string; records: DnsRecord[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="text-xs text-muted">
          <tr>
            <th scope="col" className="py-2 pr-4 font-semibold">Type</th>
            <th scope="col" className="py-2 pr-4 font-semibold">Name</th>
            <th scope="col" className="py-2 font-semibold">Value</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={`${record.record_type}:${record.name}`} className="border-t border-line align-top">
              <td className="py-2 pr-4 font-mono">{record.record_type}</td>
              <td className="py-2 pr-4 font-mono break-all">{record.name}</td>
              <td className="py-2 font-mono break-all">
                {record.value}
                {record.note !== null ? (
                  <span className="mt-1 block font-sans text-xs text-muted">{record.note}</span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChecksList({ checks }: { checks: NonNullable<DomainOut["checks"]> }) {
  return (
    <ul className="flex flex-col gap-3">
      {(Object.keys(CHECK_LABEL) as (keyof typeof CHECK_LABEL)[]).map((key) => {
        const check = checks[key];
        const state = CHECK_STATE[check.state];
        const last = formatTime(check.last_checked_at);
        const next = formatTime(check.next_check_at);
        return (
          <li key={key} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold">{CHECK_LABEL[key]}</span>
              <Tag tone={state.tone}>{state.label}</Tag>
            </div>
            {check.detail !== null ? <p className="text-sm">{check.detail}</p> : null}
            <p className="text-xs text-muted">
              {last !== null ? `Last checked ${last}.` : "Not checked yet."}
              {next !== null ? ` Next automatic check ${next}.` : ""}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The store's custom domain: claim one, publish the records it shows, and watch the three checks
 * (ownership, routing, certificate) move it to live. The sweep re-runs the checks on its own;
 * "Check now" is for the merchant who has just pasted a record (ADR-0012 §6).
 */
export function DomainCard({ canWrite }: { canWrite: boolean }) {
  const state = useAsync(getDomain, []);
  const [draft, setDraft] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"claim" | "check" | "release" | null>(null);
  const [confirmingRelease, setConfirmingRelease] = useState(false);
  const [latest, setLatest] = useState<DomainOut | null>(null);

  const domain = latest ?? (state.status === "success" ? state.data : null);

  async function claim() {
    if (busy !== null) return;
    const host = draft.trim().toLowerCase();
    setActionError(null);
    if (host === "") {
      setFieldError("Enter the domain you want buyers to use, like shop.example.com.");
      return;
    }
    setFieldError(null);
    setBusy("claim");
    try {
      setLatest(await claimDomain(host));
      setDraft("");
    } catch (error) {
      const message = claimErrorMessage(error);
      setFieldError(message.field);
      setActionError(message.banner);
    } finally {
      setBusy(null);
    }
  }

  async function checkNow() {
    if (busy !== null) return;
    setActionError(null);
    setBusy("check");
    try {
      setLatest(await checkDomain());
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        setActionError("Checks run at most once every 30 seconds. Wait a moment and try again.");
      } else {
        setActionError(error instanceof ApiError ? error.message : "Couldn't reach the server. Try again.");
      }
    } finally {
      setBusy(null);
    }
  }

  async function release() {
    if (busy !== null) return;
    setActionError(null);
    setBusy("release");
    try {
      await releaseDomain();
      setConfirmingRelease(false);
      setLatest(null);
      state.reload();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "Couldn't reach the server. Nothing changed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="p-6">
      <SectionHeader
        title="Custom domain"
        subtitle="Serve your store on your own domain. Your OpsGrids address keeps working either way."
      />

      <div className="mt-4 flex flex-col gap-4">
        {state.status === "loading" && latest === null ? (
          <div className="flex flex-col gap-3" aria-hidden="true">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : null}

        {state.status === "error" && latest === null ? (
          <ErrorState title="The domain didn't load" message={state.error.message} onRetry={state.reload} />
        ) : null}

        <div aria-live="polite" className="empty:hidden">
          {actionError !== null ? (
            <InlineAlert tone="danger" title="That didn't work">
              {actionError}
            </InlineAlert>
          ) : null}
        </div>

        {domain !== null && domain.custom_domain === null ? (
          <>
            <p className="text-sm">
              Buyers reach your store at <span className="font-mono">{domain.platform_host}</span>.
            </p>
            {canWrite ? (
              <form
                className="flex flex-col gap-3 sm:flex-row sm:items-start"
                onSubmit={(event) => {
                  event.preventDefault();
                  void claim();
                }}
              >
                <TextField
                  label="Domain"
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setFieldError(null);
                  }}
                  placeholder="shop.example.com"
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy !== null}
                  {...(fieldError !== null ? { error: fieldError } : {})}
                />
                <Button type="submit" className="sm:mt-6" loading={busy === "claim"}>
                  Connect domain
                </Button>
              </form>
            ) : null}
          </>
        ) : null}

        {domain !== null && domain.custom_domain !== null ? (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-sm break-all">{domain.custom_domain}</span>
              {domain.status !== null ? (
                <Tag tone={STATUS[domain.status].tone}>{STATUS[domain.status].label}</Tag>
              ) : null}
            </div>

            {domain.verification !== null ? (
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold">1. Prove you own it</h3>
                <p className="text-sm text-muted">
                  Add this TXT record at your DNS provider. It stays valid until{" "}
                  {formatTime(domain.verification.claim_expires_at)}.
                </p>
                <RecordsTable
                  caption="Verification record"
                  records={[
                    {
                      record_type: domain.verification.record_type,
                      name: domain.verification.name,
                      value: domain.verification.value,
                      note: null,
                    },
                  ]}
                />
              </section>
            ) : null}

            {domain.dns !== null ? (
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold">2. Point it at OpsGrids</h3>
                <RecordsTable caption="Routing record" records={[domain.dns.preferred]} />
                {domain.dns.fallback.length > 0 ? (
                  <>
                    <p className="text-sm text-muted">
                      If your provider can't create that record, use this instead:
                    </p>
                    <RecordsTable caption="Fallback routing records" records={domain.dns.fallback} />
                  </>
                ) : null}
                <p className="text-xs text-muted">{domain.dns.proxy_warning}</p>
                {domain.dns.also_served.length > 0 ? (
                  <p className="text-xs text-muted">
                    Also served: <span className="font-mono">{domain.dns.also_served.join(", ")}</span>
                  </p>
                ) : null}
              </section>
            ) : null}

            {domain.checks !== null ? (
              <section className="flex flex-col gap-2" aria-live="polite">
                <h3 className="text-sm font-semibold">3. Checks</h3>
                <ChecksList checks={domain.checks} />
              </section>
            ) : null}

            {canWrite ? (
              <div className="flex flex-wrap gap-3">
                <Button variant="outline" loading={busy === "check"} disabled={busy !== null} onClick={() => void checkNow()}>
                  Check now
                </Button>
                <Button variant="outline" disabled={busy !== null} onClick={() => setConfirmingRelease(true)}>
                  Disconnect
                </Button>
              </div>
            ) : null}
          </>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirmingRelease}
        onClose={() => {
          setConfirmingRelease(false);
          setActionError(null);
        }}
        onConfirm={() => void release()}
        title="Disconnect this domain?"
        body="Buyers visiting it stop reaching your store straight away. Your OpsGrids address keeps working. To use the domain again you'll need to verify it again."
        confirmLabel="Disconnect"
        pendingLabel="Disconnecting…"
        pending={busy === "release"}
        {...(actionError !== null ? { error: actionError } : {})}
      />
    </Card>
  );
}
