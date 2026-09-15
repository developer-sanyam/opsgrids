import { Link } from "react-router-dom";

import { getCompliancePosition } from "@/api/client";
import type { CompliancePosition } from "@/api/client";
import type { Customer, Invoice } from "@/api/types";
import { filingErrorSentence } from "@/data/compliance";
import { customerName } from "@/data/customers";
import { readErrorMessage } from "@/lib/errors";
import { formatDate } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { useAsync } from "@opsgrids/client";
import {
  AlertOctagonIcon,
  Card,
  CheckCircleIcon,
  ErrorState,
  SectionHeader,
  ShieldCheckIcon,
  Skeleton,
  cn,
} from "@opsgrids/ui";
import { FilingStatusPill, SampleDataChip } from "./ComplianceStatus";

/** One headline figure. The qualifier under it is what makes the number mean something. */
function Figure({
  value,
  label,
  qualifier,
  tone = "neutral",
}: {
  value: string;
  label: string;
  qualifier: string;
  tone?: "neutral" | "attention";
}) {
  return (
    <div className="flex flex-col gap-0.5 rounded-tile bg-ground px-4 py-3">
      <span
        className={cn(
          "money text-2xl font-extrabold tnum",
          tone === "attention" ? "text-danger" : "text-ink",
        )}
      >
        {value}
      </span>
      <span className="text-sm font-semibold text-ink-soft">{label}</span>
      <span className="text-xs text-muted">{qualifier}</span>
    </div>
  );
}

/** One document the platform has given up on. Links straight to the panel that explains it. */
function StuckRow({
  invoice,
  customerById,
}: {
  invoice: Invoice;
  customerById: ReadonlyMap<string, Customer> | undefined;
}) {
  return (
    <Link
      to={`/invoices/${invoice.id}`}
      className="-mx-2 flex items-start gap-3 rounded-tile px-2 py-2.5 transition-colors hover:bg-ground focus-visible:bg-ground"
    >
      <AlertOctagonIcon className="mt-0.5 size-5 shrink-0 text-danger" />
      <div className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="text-sm font-bold text-ink tnum">
          {invoice.number}
          <span className="ml-2 font-medium text-muted">
            {customerName(customerById, invoice.customer_id)}
          </span>
        </span>
        <span className="truncate text-xs text-muted">
          {(invoice.einvoice ? filingErrorSentence(invoice.einvoice) : null) ??
            "The portal refused this document."}
        </span>
        <span className="text-xs text-faint tnum">
          Issued {formatDate(invoice.issued_date)} · {formatMoney(invoice.total_minor)}
        </span>
      </div>
      <FilingStatusPill state={invoice.einvoice} />
    </Link>
  );
}

function Position({
  position,
  customerById,
}: {
  position: CompliancePosition;
  customerById: ReadonlyMap<string, Customer> | undefined;
}) {
  const { summary, imsOutstanding } = position;
  const unfiled = Math.max(0, summary.required - summary.filed);

  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Figure
          value={`${summary.filed}/${summary.required}`}
          label="Registered with the portal"
          qualifier={`Documents raised in ${summary.periodLabel} that need an IRN`}
        />
        <Figure
          value={String(unfiled)}
          label="Still unfiled"
          qualifier={
            summary.awaiting > 0
              ? `${summary.awaiting} the platform is still working on`
              : "Nothing left with the filing agent"
          }
          tone={unfiled > 0 ? "attention" : "neutral"}
        />
        <Figure
          value={imsOutstanding === null ? "—" : String(imsOutstanding)}
          label="Awaiting your decision"
          qualifier={
            imsOutstanding === null
              ? "The reconciliation queue could not be read"
              : "Supplier documents nobody has decided"
          }
        />
      </div>

      {summary.terminal.length === 0 ? (
        <p className="flex items-center gap-2 py-3 text-sm text-muted">
          <CheckCircleIcon className="size-4 text-positive" />
          Nothing is stuck. Every document is either registered or still with the filing agent.
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          <p className="pt-1 text-sm font-semibold text-ink">
            {summary.terminal.length === 1
              ? "One document needs you"
              : `${summary.terminal.length} documents need you`}
            <span className="ml-1.5 font-medium text-muted">
              — the platform has stopped trying on these.
            </span>
          </p>
          <div className="flex flex-col divide-y divide-line/70">
            {summary.terminal.slice(0, 4).map((invoice) => (
              <StuckRow key={invoice.id} invoice={invoice} customerById={customerById} />
            ))}
          </div>
        </div>
      )}

      {summary.unreported > 0 ? (
        <p className="text-xs text-muted">
          {summary.unreported} document{summary.unreported === 1 ? "" : "s"} came back without a
          filing position, so nothing is claimed about {summary.unreported === 1 ? "it" : "them"}.
        </p>
      ) : null}
    </>
  );
}

/**
 * The compliance position on the overview: filing for the current return period, anything the
 * platform has given up on, and how many supplier documents are waiting on a person.
 *
 * Deliberately the same shape as "Needs attention" beside it — a queue of things to do, not a
 * dashboard of things to admire.
 */
export function ComplianceCard({
  customerById,
}: {
  customerById: ReadonlyMap<string, Customer> | undefined;
}) {
  const position = useAsync(getCompliancePosition, []);

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader
        title="GST compliance"
        subtitle={
          position.status === "success"
            ? `E-invoicing for ${position.data.summary.periodLabel}, and what suppliers filed against you`
            : "E-invoicing, and what suppliers filed against you"
        }
        action={
          <span className="flex items-center gap-3">
            <SampleDataChip />
            <Link
              to="/compliance/ims"
              className="inline-flex items-center gap-1.5 text-sm font-semibold text-accent transition-colors hover:text-accent-strong"
            >
              <ShieldCheckIcon className="size-4" />
              Reconciliation queue
            </Link>
          </span>
        }
      />

      {position.status === "loading" ? (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full rounded-tile" />
            ))}
          </div>
          <Skeleton className="h-14 w-full rounded-tile" />
        </div>
      ) : null}
      {position.status === "error" ? (
        <ErrorState message={readErrorMessage(position.error, "the compliance position")} />
      ) : null}
      {position.status === "success" ? (
        <Position position={position.data} customerById={customerById} />
      ) : null}
    </Card>
  );
}
