import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { StatusBadge } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

import { listDomains } from "@/api/client";
import type { DnsRecordKind, DnsRecordOut, DnsRecordStatus, DomainOut } from "@/api/types";
import { isNotActivated } from "@/lib/errors";
import { readableInstruction } from "@/lib/records";
import { ErrorState, Skeleton } from "@/ui";

/**
 * Sending domain authentication: for each sending domain, whether SPF, DKIM and DMARC are published,
 * read from the existing domains API — the same records and the same statuses the Domains screen
 * shows, in the words the settings design uses. Adding or fixing a record happens on the Domains
 * screen, where the exact value to paste is; this card links there rather than restating it.
 */

const KIND_LABEL: Record<DnsRecordKind, string> = { spf: "SPF", dkim: "DKIM", dmarc: "DMARC" };

const STATUS: Record<DnsRecordStatus, { label: string; tone: StatusTone }> = {
  verified: { label: "Verified", tone: "success" },
  missing: { label: "Not set", tone: "warning" },
  multiple: { label: "Multiple records", tone: "warning" },
  mismatch: { label: "Needs an edit", tone: "warning" },
};

function RecordRow({ record }: { record: DnsRecordOut }) {
  const { label, tone } = STATUS[record.status];
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-3 border-t border-line py-3 first:border-t-0">
      <span className="w-16 text-sm font-bold text-ink">{KIND_LABEL[record.kind]}</span>
      <span className="min-w-0 basis-full break-words text-sm text-muted sm:basis-0 sm:flex-1">
        {readableInstruction(record.instruction)}
      </span>
      <StatusBadge tone={tone} label={label} />
      {record.status !== "verified" ? (
        <Link
          to="/domains"
          className="relative hit-44 inline-flex w-full items-center justify-center rounded-tile border border-field-edge px-3 text-sm font-semibold text-accent-ink hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-auto sm:border-0 sm:underline-offset-4 sm:hover:bg-transparent sm:hover:underline"
        >
          Add record
        </Link>
      ) : null}
    </li>
  );
}

function DomainBlock({ domain }: { domain: DomainOut }) {
  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-sm font-semibold text-ink">{domain.domain}</h4>
      {domain.records.length === 0 ? (
        <p className="text-sm text-muted">
          No records to publish yet. Connect your email provider first, then check this domain again.
        </p>
      ) : (
        <ul>
          {domain.records.map((record) => (
            <RecordRow key={record.kind} record={record} />
          ))}
        </ul>
      )}
    </div>
  );
}

export function SendingAuthCard() {
  const state = useAsync(listDomains, []);

  return (
    <section aria-labelledby="sending-auth" className="flex flex-col gap-3">
      <h3 id="sending-auth" className="text-base font-bold text-ink">
        Sending domain authentication
      </h3>

      {state.status === "loading" ? (
        <div className="flex flex-col gap-2">
          <span className="sr-only" aria-live="polite">
            Loading sending domains
          </span>
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-16 w-full rounded-tile" />
        </div>
      ) : null}

      {state.status === "error" ? (
        <ErrorState
          title={isNotActivated(state.error) ? "Sending domains aren't available yet" : "Couldn't load your sending domains"}
          message={state.error.message}
          onRetry={state.reload}
        />
      ) : null}

      {state.status === "success" && state.data.items.length === 0 ? (
        <p className="text-sm text-muted">
          No sending domain yet. <Link to="/domains" className="font-semibold text-accent-ink underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Add a domain</Link> to
          publish SPF, DKIM and DMARC and send as your own address.
        </p>
      ) : null}

      {state.status === "success" && state.data.items.length > 0 ? (
        <div className="flex flex-col gap-4">
          {state.data.items.map((domain) => (
            <DomainBlock key={domain.id} domain={domain} />
          ))}
        </div>
      ) : null}
    </section>
  );
}
