import { useEffect, useState } from "react";

import { CheckCircleIcon, CheckIcon, CircleDotIcon, ClipboardListIcon } from "@opsgrids/ui";

import type { DnsRecordOut } from "@/api/types";
import { dnsRecordLabel } from "@/lib/format";
import { requiredProgress } from "@/lib/records";
import { useAnnounce } from "@/ui";

/**
 * The records a customer publishes at their registrar, rendered verbatim.
 *
 * This is the one table in this product whose output is retyped into another system, so nothing
 * here truncates a value, reflows it or prettifies it — a DKIM public key with a line break
 * inserted for looks is a record that will never verify, and the customer will blame their
 * registrar. Each value is copyable for the same reason: two hundred base64 characters selected
 * by hand is where a setup goes wrong silently.
 *
 * **Required and recommended are marked on every row.** `domain.fold_domain_status` folds a
 * domain's status from the required records alone, so a customer staring at an unverified DMARC
 * row needs to know from the row itself that it is not what is holding them up.
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

/** Whether DNS has this record yet. The icon repeats what the word says — never replaces it. */
function RecordState({ verified }: { verified: boolean }) {
  return verified ? (
    <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-positive">
      <CheckCircleIcon className="size-4" />
      Published
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-muted">
      <CircleDotIcon className="size-4" />
      Not found
    </span>
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
        {record.host}
        <span className="mt-1 block font-sans">
          <CopyButton value={record.host} label={`${name} host`} />
        </span>
      </td>
      <td className="py-3 pr-4 font-mono text-xs break-all text-ink">
        {/* An empty value is a real state: the DKIM record carries no value until the service has
            minted the keypair. Saying so beats rendering an empty cell that reads as a bug. */}
        {record.value === "" ? (
          <span className="font-sans text-muted">Generated when the domain is added</span>
        ) : (
          <>
            {record.value}
            <span className="mt-1 block font-sans">
              <CopyButton value={record.value} label={`${name} value`} />
            </span>
          </>
        )}
      </td>
      <td className="py-3 whitespace-nowrap">
        <RecordState verified={record.verified} />
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

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">
        <span className="font-semibold text-ink tnum">
          {found} of {total} required records found.
        </span>{" "}
        DMARC is checked and reported here too, but it is not one of them — a domain whose
        security team has not settled a policy still sends.
      </p>

      <div className="overflow-x-auto">
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
                Host
              </th>
              <th scope="col" className="pb-2 pr-4">
                Value
              </th>
              <th scope="col" className="pb-2">
                In DNS
              </th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
              <RecordRow key={record.kind} record={record} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
