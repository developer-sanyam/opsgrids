import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { GlobeIcon, RefreshIcon } from "@opsgrids/ui";

import { addDomain, listDomains, verifyDomain } from "@/api/client";
import type { DomainOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import { requiredProgress } from "@/lib/records";
import {
  Announcer,
  Button,
  Card,
  DomainStatusTag,
  EmptyState,
  ErrorState,
  InlineAlert,
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
    const value = domain.trim().toLowerCase();
    if (value === "") {
      setError("Which domain should mail come from?");
      setFailure(null);
      return;
    }
    // Whether a name is a domain the customer controls is a DNS question, and the service asks
    // DNS. Refusing a plausible-looking string here would only invent a second, weaker rule.
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
            : "The domain your mail should come from. Engage generates the five records to publish and checks for them until they resolve."
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
          placeholder="mail.your-company.com"
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

function DomainPanel({
  domain,
  onVerified,
}: {
  domain: DomainOut;
  onVerified: (domain: DomainOut) => void;
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
              {domain.last_checked_at === null
                ? "DNS has not been checked yet"
                : `DNS last checked ${formatInstant(domain.last_checked_at)}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <DomainStatusTag status={domain.status} />
          <Button
            variant="outline"
            size="sm"
            onClick={() => void verify()}
            disabled={checking}
            leadingIcon={<RefreshIcon className="size-4" />}
          >
            {checking ? "Checking DNS…" : "Verify"}
          </Button>
        </div>
      </div>

      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title={`${domain.domain} wasn't checked`}>
            {failure}
          </InlineAlert>
        ) : null}
      </div>

      <DomainRecordsTable records={domain.records} domainName={domain.domain} />
    </Card>
  );
}

const NO_DOMAINS: DomainOut[] = [];

function Domains() {
  const [params] = useSearchParams();
  const query = params.get("q") ?? "";

  const [attempt, setAttempt] = useState(0);
  /* Domains a check has rewritten since the last read. The verify route answers with the whole
     domain, so the screen shows what DNS just said instead of asking for the list again — and
     the map is dropped on a reload, where the server's own answer is the fresher one. */
  const [checked, setChecked] = useState<Record<string, DomainOut>>({});

  const loader = useCallback(() => listDomains(), []);
  const state = useAsync(loader, [attempt]);
  const loaded = state.status === "success" ? state.data.items : NO_DOMAINS;

  const domains = useMemo(
    () => loaded.map((domain) => checked[domain.id] ?? domain),
    [loaded, checked],
  );

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q === "" ? domains : domains.filter((d) => d.domain.toLowerCase().includes(q));
  }, [domains, query]);

  function reload() {
    setChecked({});
    setAttempt((current) => current + 1);
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
        <Card className="p-6">
          <ErrorState message={state.error.message} title="Couldn't load your domains" />
        </Card>
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
          onVerified={(next) => setChecked((current) => ({ ...current, [next.id]: next }))}
        />
      ))}
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
