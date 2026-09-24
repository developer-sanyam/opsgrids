import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Can, useAsync } from "@opsgrids/client";
import { GlobeIcon, RefreshIcon } from "@opsgrids/ui";

import { addDomain, deleteDomain, editDomain, listDomains, verifyDomain } from "@/api/client";
import type { DomainOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import { requiredProgress } from "@/lib/records";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  DomainStatusTag,
  EmptyState,
  InlineAlert,
  LoadFailure,
  PageHeader,
  SectionHeader,
  Skeleton,
  TextField,
  useAnnounce,
} from "@/ui";
import { DomainRecordsTable } from "./DomainRecords";

/**
 * The sending domains, the exact records to publish for each one, and the check that folds DNS
 * back into a status.
 *
 * `?q=` is the sidebar's search. It filters by domain name only: the record values are base64 and
 * boilerplate, and matching inside them would return every domain for a query that appears in
 * every domain's SPF line.
 */

function AddDomainForm({ hasDomains, onAdded }: { hasDomains: boolean; onAdded: () => void }) {
  const announce = useAnnounce();
  const [domain, setDomain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (submitting) return;
    const value = domain.trim();
    if (value === "") {
      setError("Which domain should mail come from?");
      setFailure(null);
      return;
    }
    // Normalising and validating a domain name is the service's job (`dns_check.
    // normalize_domain_name`): it strips a pasted URL down to its bare domain, and refuses
    // anything that is not a plain public hostname with one sentence a customer can act on.
    setError(null);
    setSubmitting(true);
    setFailure(null);
    try {
      const added = await addDomain({ domain: value });
      setDomain("");
      announce(`${added.domain} added. Publish its DNS records, then verify it.`);
      onAdded();
    } catch (caught) {
      // The typed name stays: a rejected domain is usually a domain typed nearly right.
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader
        title={hasDomains ? "Add another domain" : "Add your sending domain"}
        subtitle={
          hasDomains
            ? "Each domain gets its own records to publish and is verified on its own."
            : "Just the domain, like example.com — Engage works out what to publish once your provider is connected."
        }
      />

      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That domain wasn't added">
            {failure}
          </InlineAlert>
        ) : null}
      </div>

      <form
        className="flex flex-col gap-3 sm:flex-row sm:items-start"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextField
          label="Domain"
          value={domain}
          onChange={(event) => {
            setDomain(event.target.value);
            if (error !== null) setError(null);
          }}
          placeholder="example.com"
          helper="Enter just the domain, like example.com"
          autoComplete="off"
          spellCheck={false}
          disabled={submitting}
          containerClassName="flex-1"
          {...(error !== null ? { error } : {})}
        />
        {/* Aligned to the field's box rather than its label row, which is what the top padding
            buys on the wide layout. */}
        <Button type="submit" disabled={submitting} className="sm:mt-6">
          {submitting ? "Adding…" : "Add domain"}
        </Button>
      </form>
    </Card>
  );
}

/** The DKIM selector and SPF include a customer names for themselves, for a domain sent through
 *  their own SMTP provider. Neither is knowable from the connection kind alone the way Google's
 *  and Microsoft's are. */
function SmtpProviderSettings({
  domain,
  onSaved,
}: {
  domain: DomainOut;
  onSaved: (domain: DomainOut) => void;
}) {
  const announce = useAnnounce();
  const [selector, setSelector] = useState(domain.dkim_selector);
  const [include, setInclude] = useState(domain.spf_include ?? "");
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const unchanged = selector === domain.dkim_selector && include === (domain.spf_include ?? "");

  async function save() {
    if (saving || unchanged) return;
    setSaving(true);
    setFailure(null);
    try {
      const updated = await editDomain(domain.id, {
        dkim_selector: selector,
        spf_include: include,
      });
      announce(`${updated.domain}'s provider settings saved. Publish the updated records, then verify.`);
      onSaved(updated);
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-tile bg-ground p-4">
      <SectionHeader
        title="Your provider's DKIM and SPF"
        subtitle="Amazon SES, SendGrid, Mailgun and similar providers each publish their own DKIM record and name their own SPF host. Enter what your provider's dashboard gives you."
      />
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="Those settings weren't saved">
            {failure}
          </InlineAlert>
        ) : null}
      </div>
      <form
        className="flex flex-col gap-3 sm:flex-row sm:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <TextField
          label="DKIM selector"
          value={selector}
          onChange={(event) => setSelector(event.target.value)}
          placeholder="sg1"
          autoComplete="off"
          spellCheck={false}
          disabled={saving}
          containerClassName="flex-1"
        />
        <TextField
          label="SPF include"
          value={include}
          onChange={(event) => setInclude(event.target.value)}
          placeholder="amazonses.com"
          autoComplete="off"
          spellCheck={false}
          disabled={saving}
          containerClassName="flex-1"
        />
        <Button type="submit" variant="outline" disabled={saving || unchanged}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </form>
    </div>
  );
}

function DomainPanel({
  domain,
  onVerified,
  onEdited,
  onDeleteRequested,
}: {
  domain: DomainOut;
  onVerified: (domain: DomainOut) => void;
  onEdited: (domain: DomainOut) => void;
  onDeleteRequested: (domain: DomainOut) => void;
}) {
  const announce = useAnnounce();
  const [checking, setChecking] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function verify() {
    if (checking) return;
    setChecking(true);
    setFailure(null);
    try {
      const checked = await verifyDomain(domain.id);
      const { found, total } = requiredProgress(checked.records);
      announce(
        checked.status === "verified"
          ? `${checked.domain} is verified and can send.`
          : `${checked.domain}: ${found} of ${total} required records found.`,
      );
      // The whole domain replaces the one held: the check rewrites `records` and `status`
      // together, and merging the two would let a stale status outlive the records it came from.
      onVerified(checked);
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setChecking(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-11 shrink-0 place-items-center rounded-tile bg-ground text-muted">
            <GlobeIcon className="size-5" />
          </span>
          <div className="flex min-w-0 flex-col">
            <h2 className="truncate text-lg font-bold tracking-tight">{domain.domain}</h2>
            <p className="text-sm text-muted">
              {domain.connection_kind === null
                ? "No email provider connected"
                : domain.last_checked_at === null
                  ? "DNS has not been checked yet"
                  : `DNS last checked ${formatInstant(domain.last_checked_at)}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <DomainStatusTag status={domain.status} />
          <Can permission="engage.setup.domain.write" scope="ORG">
            {domain.connection_kind !== null ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void verify()}
                disabled={checking}
                leadingIcon={<RefreshIcon className="size-4" />}
              >
                {checking ? "Checking DNS…" : "Verify"}
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              className="text-negative"
              onClick={() => onDeleteRequested(domain)}
            >
              Delete
            </Button>
          </Can>
        </div>
      </div>

      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title={`${domain.domain} wasn't checked`}>
            {failure}
          </InlineAlert>
        ) : null}
      </div>

      {domain.connection_kind === null ? (
        <InlineAlert tone="info" title="Connect your email provider first" live="off">
          Engage generates this domain's records from how you send. <Link to="/connection">Connect a provider</Link>{" "}
          to see what to publish.
        </InlineAlert>
      ) : (
        <>
          {domain.connection_kind === "smtp" ? (
            <SmtpProviderSettings domain={domain} onSaved={onEdited} />
          ) : null}
          <DomainRecordsTable records={domain.records} domainName={domain.domain} />
        </>
      )}
    </Card>
  );
}

const NO_DOMAINS: DomainOut[] = [];

function Domains() {
  const [params] = useSearchParams();
  const query = params.get("q") ?? "";

  const [attempt, setAttempt] = useState(0);
  /* Domains a check or an edit has rewritten since the last read. The verify and edit routes
     answer with the whole domain, so the screen shows what the server just said instead of
     asking for the list again — and the map is dropped on a reload, where the server's own
     answer is the fresher one. */
  const [changed, setChanged] = useState<Record<string, DomainOut>>({});
  const [deleting, setDeleting] = useState<DomainOut | null>(null);
  const [deleteFailure, setDeleteFailure] = useState<string | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const announce = useAnnounce();

  const loader = useCallback(() => listDomains(), []);
  const state = useAsync(loader, [attempt]);
  const loaded = state.status === "success" ? state.data.items : NO_DOMAINS;

  const domains = useMemo(
    () => loaded.map((domain) => changed[domain.id] ?? domain),
    [loaded, changed],
  );

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q === "" ? domains : domains.filter((d) => d.domain.toLowerCase().includes(q));
  }, [domains, query]);

  function reload() {
    setChanged({});
    setAttempt((current) => current + 1);
  }

  async function confirmDelete() {
    if (deleting === null || deletePending) return;
    setDeletePending(true);
    setDeleteFailure(null);
    try {
      await deleteDomain(deleting.id);
      const name = deleting.domain;
      setDeleting(null);
      announce(`${name} deleted.`);
      reload();
    } catch (caught) {
      setDeleteFailure(failureMessage(caught));
    } finally {
      setDeletePending(false);
    }
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Email"
        title="Domains"
        description="Publish these records at your registrar and the domain becomes yours to send from. DMARC is recommended rather than required — a domain sends without it."
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your domains" />
      ) : null}

      {/* The form is the empty state when there is nothing yet: a panel saying "no domains"
          above the field that adds one is the same sentence twice, and only one of them acts. */}
      {state.status === "success" ? (
        <AddDomainForm hasDomains={domains.length > 0} onAdded={reload} />
      ) : null}

      {state.status === "success" && domains.length > 0 && shown.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<GlobeIcon className="size-6" />}
            title="No matching domains"
            description={`Nothing matches “${query}”. Clear the search to see all ${domains.length} domains.`}
          />
        </Card>
      ) : null}

      {shown.map((domain) => (
        <DomainPanel
          key={domain.id}
          domain={domain}
          onVerified={(next) => setChanged((current) => ({ ...current, [next.id]: next }))}
          onEdited={(next) => setChanged((current) => ({ ...current, [next.id]: next }))}
          onDeleteRequested={setDeleting}
        />
      ))}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          setDeleteFailure(null);
        }}
        onConfirm={() => void confirmDelete()}
        title="Delete this domain?"
        body={
          deleting === null
            ? ""
            : `${deleting.domain} and every sender on it are removed. This can't be undone. A domain used by a campaign that is not a draft or cancelled cannot be deleted${
                deleting.connection_kind !== null
                  ? ""
                  : " — connect an email provider to see whether it is in use"
              }.`
        }
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={deletePending}
        {...(deleteFailure !== null ? { error: deleteFailure } : {})}
      />
    </div>
  );
}

/** The screen's single polite live region — every panel and copy button announces into it. */
export function DomainsPage() {
  return (
    <Announcer>
      <Domains />
    </Announcer>
  );
}
