import { useEffect, useState } from "react";

import { CheckCircleIcon, CheckIcon, CircleDotIcon, ClipboardListIcon } from "@opsgrids/ui";

import type { DnsRecordOut, DnsRecordStatus } from "@/api/types";
import { dnsRecordLabel } from "@/lib/format";
import { requiredProgress } from "@/lib/records";
import { InlineAlert, useAnnounce } from "@/ui";

/**
 * The records a customer publishes at their registrar, rendered verbatim.
 *
 * This is the one table in this product whose output is retyped into another system, so nothing
 * here truncates a value, reflows it or prettifies it — a DKIM public key with a line break
 * inserted for looks is a record that will never verify, and the customer will blame their
 * registrar. Each pasteable value is copyable for the same reason.
 *
 * **Required and recommended are marked on every row.** `domain.fold_domain_status` folds a
 * domain's status from the required records alone, so a customer staring at an unpublished DMARC
 * row needs to know from the row itself that it is not what is holding them up.
 *
 * Neither an exact DKIM value nor a Microsoft CNAME target is ever known here — the provider
 * mints the key, not this platform — so those rows show the setup instructions in place of a
 * value to copy. Only SPF ever carries a `suggested_value`, and only SPF's row distinguishes
 * "add this record" from "edit your existing one": every other kind is a plain publish.
 */

function CopyButton({ value, label }: { value: string; label: string }) {
  const announce = useAnnounce();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(id);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      announce(`${label} copied.`);
    } catch {
      // No clipboard permission, or an insecure origin. The value is on screen and selectable,
      // so the honest answer is to say so rather than to leave a button that did nothing.
      announce(`Couldn't copy the ${label.toLowerCase()}. Select it and copy it yourself.`);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={`Copy ${label}`}
      className="inline-flex h-7 items-center gap-1 rounded-full px-2 text-xs font-semibold text-muted transition-colors hover:bg-ground hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {copied ? (
        <CheckIcon className="size-3.5 text-positive" />
      ) : (
        <ClipboardListIcon className="size-3.5" />
      )}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

const STATUS_LABEL: Record<DnsRecordStatus, string> = {
  verified: "Published",
  missing: "Not found",
  multiple: "Multiple records",
  mismatch: "Needs an edit",
};

/** Whether DNS has this record yet — and if not, whether nothing is there or something wrong
 *  is. The icon repeats what the word says — never replaces it. */
function RecordStatusTag({ status }: { status: DnsRecordStatus }) {
  if (status === "verified") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-positive">
        <CheckCircleIcon className="size-4" />
        {STATUS_LABEL[status]}
      </span>
    );
  }
  const tone = status === "missing" ? "text-muted" : "text-late";
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-semibold ${tone}`}>
      <CircleDotIcon className="size-4" />
      {STATUS_LABEL[status]}
    </span>
  );
}

/** A value that names itself as generated elsewhere rather than something to paste — Google's
 *  and Microsoft's own DKIM values, and the two SMTP placeholders shown before a selector or an
 *  include has been entered. Wrapped in parentheses by `domain.expected_records`, on purpose, so
 *  this check is a single, cheap convention rather than a list of exact strings to keep in step
 *  with the service. */
function isPasteable(value: string): boolean {
  return value !== "" && !value.startsWith("(");
}

/** The verb this row's value needs, for SPF only — every other kind is a plain publish with no
 *  "edit vs add" distinction to make. */
function spfDirective(status: DnsRecordStatus): string {
  switch (status) {
    case "missing":
      return "Add this record:";
    case "mismatch":
      return "Edit your existing SPF record to this value — don't add a second one:";
    case "multiple":
      return "Replace every one of your SPF records with this single one:";
    case "verified":
      return "Currently published:";
  }
}

function ValueBlock({ record }: { record: DnsRecordOut }) {
  if (record.kind === "spf") {
    const value = record.suggested_value ?? record.value;
    return (
      <div className="flex flex-col gap-1">
        <span className="font-sans text-xs font-semibold text-ink-soft">
          {spfDirective(record.status)}
        </span>
        {value === "" ? (
          <span className="font-sans text-muted">Enter your provider's SPF include below.</span>
        ) : (
          <>
            <span className="break-all">{value}</span>
            <span className="mt-1 block font-sans">
              <CopyButton value={value} label="SPF record" />
            </span>
          </>
        )}
      </div>
    );
  }

  if (!isPasteable(record.value)) {
    return <span className="font-sans text-muted">{record.value || "Enter this below"}</span>;
  }

  return (
    <>
      <span className="break-all">{record.value}</span>
      <span className="mt-1 block font-sans">
        <CopyButton value={record.value} label={`${dnsRecordLabel(record.kind)} value`} />
      </span>
    </>
  );
}

function RecordRow({ record }: { record: DnsRecordOut }) {
  const name = dnsRecordLabel(record.kind);
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left font-semibold">
        <span className="block text-sm text-ink">{name}</span>
        <span
          className={`block text-xs font-medium ${record.required ? "text-ink-soft" : "text-muted"}`}
        >
          {record.required ? "Required" : "Recommended — doesn't block sending"}
        </span>
      </th>
      <td className="py-3 pr-4 text-sm font-semibold tnum">{record.type}</td>
      <td className="py-3 pr-4 font-mono text-xs break-all text-ink">
        <span className="block">
          {record.registrar_name}
          <span className="mt-1 block font-sans">
            <CopyButton value={record.registrar_name} label={`${name} name`} />
          </span>
        </span>
        <span className="mt-2 block font-sans text-xs text-faint">{record.fqdn}</span>
      </td>
      <td className="py-3 pr-4 font-mono text-xs break-all text-ink">
        <ValueBlock record={record} />
        {/* SPF's own directive line already says what to do; its `instruction` either repeats
            that (mismatch, missing) or is surfaced separately as the multiple-records banner
            (`DomainRecordsTable` above) — showing it again here would be the same sentence
            twice. Every other kind has no directive line, so its instruction is the only
            explanation on the row. */}
        {record.kind === "spf" ? null : (
          <p className="mt-2 max-w-sm font-sans text-xs text-muted">{record.instruction}</p>
        )}
      </td>
      <td className="py-3 whitespace-nowrap">
        <RecordStatusTag status={record.status} />
      </td>
    </tr>
  );
}

export function DomainRecordsTable({
  records,
  domainName,
}: {
  records: DnsRecordOut[];
  domainName: string;
}) {
  const { found, total } = requiredProgress(records);
  const multipleSpf = records.find((r) => r.kind === "spf" && r.status === "multiple");

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">
        <span className="font-semibold text-ink tnum">
          {found} of {total} required records found.
        </span>{" "}
        DMARC is checked and reported here too, but it is not one of them — a domain whose
        security team has not settled a policy still sends.
      </p>

      {multipleSpf !== undefined ? (
        <InlineAlert tone="danger" title="Your domain has more than one SPF record">
          {multipleSpf.instruction}
        </InlineAlert>
      ) : null}

      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[52rem] border-collapse text-left">
          <caption className="sr-only">
            DNS records to publish for {domainName}, whether each is required, and whether it has
            been found
          </caption>
          <thead>
            <tr className="text-xs font-bold uppercase tracking-wider text-faint">
              <th scope="col" className="pb-2 pr-4">
                Record
              </th>
              <th scope="col" className="pb-2 pr-4">
                Type
              </th>
              <th scope="col" className="pb-2 pr-4">
                Name
              </th>
              <th scope="col" className="pb-2 pr-4">
                Value
              </th>
              <th scope="col" className="pb-2">
                Status
              </th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
              <RecordRow key={`${record.kind}-${record.fqdn}`} record={record} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
