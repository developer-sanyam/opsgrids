import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

import { listFilings } from "@/api/client";
import type { EInvoiceFiling, EInvoiceState, Invoice } from "@/api/types";
import {
  CANCELLATION_REASONS,
  filingErrorSentence,
  filingRowLabel,
  filingView,
  remedyView,
} from "@/data/compliance";
import { readErrorMessage } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { Can, useAsync } from "@opsgrids/client";
import {
  AlertOctagonIcon,
  Button,
  Card,
  CheckCircleIcon,
  ClipboardCheckIcon,
  ClipboardListIcon,
  ClockIcon,
  ErrorState,
  QrCode,
  SectionHeader,
  Skeleton,
  Tag,
  cn,
} from "@opsgrids/ui";
import { FilingStatusPill, SampleDataChip } from "./ComplianceStatus";

const SUPPLY_TYPE_LABEL: Record<string, string> = {
  b2b: "B2B — registered buyer",
  b2c: "B2C — unregistered buyer",
  export: "Export",
  sez: "SEZ supply",
};

/** One labelled fact in the GST block. `mono` for identifiers, which are read character by character. */
function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-semibold uppercase tracking-wide text-faint">{label}</dt>
      <dd className={cn("text-sm text-ink-soft", mono ? "money break-all" : "font-medium")}>
        {value}
      </dd>
    </div>
  );
}

/** Copy one identifier to the clipboard, and say so. The IRN is 64 characters — nobody retypes it. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  const copy = useCallback(() => {
    // Not every context grants clipboard access (an insecure origin, a locked-down browser). The
    // failure is told to the user rather than swallowed, because the alternative is a button that
    // silently does nothing.
    void navigator.clipboard
      ?.writeText(value)
      .then(() => {
        setCopied(true);
        setFailed(false);
        window.setTimeout(() => setCopied(false), 2400);
      })
      .catch(() => setFailed(true));
  }, [value]);

  return (
    <span className="inline-flex items-center gap-2">
      <Button
        variant="ghost"
        size="sm"
        onClick={copy}
        leadingIcon={
          copied ? (
            <ClipboardCheckIcon className="size-4" />
          ) : (
            <ClipboardListIcon className="size-4" />
          )
        }
      >
        {copied ? "Copied" : label}
      </Button>
      <span aria-live="polite" className="text-xs text-muted">
        {failed ? "Could not reach the clipboard — select the text and copy it." : null}
      </span>
    </span>
  );
}

/** The signed QR the invoice must print, rendered from the payload the portal actually returned. */
function SignedQr({ payload, number }: { payload: string; number: string }) {
  return (
    <div className="flex flex-col items-center gap-2">
      {/* 240px across a 97-module grid is about 2.5 device-independent pixels per module — the
          floor below which a phone camera starts guessing. The SVG scales, so zooming helps too. */}
      <div className="rounded-tile bg-white p-3 shadow-card">
        <QrCode
          value={payload}
          level="M"
          size={240}
          label={`Signed e-invoice QR code for ${number}`}
        />
      </div>
      <p className="max-w-[16rem] text-center text-xs text-muted">
        The signed code, exactly as the portal returned it. This is what the printed invoice
        carries.
      </p>
    </div>
  );
}

/**
 * The attempt history: append-only, newest first.
 *
 * A list rather than a status, because that is what the table behind it is. Five rows of
 * "the portal did not answer" followed by one that says the platform stopped is a story an
 * operator can act on; a single field reading `failed` is not.
 */
function FilingTimeline({ filings }: { filings: EInvoiceFiling[] }) {
  if (filings.length === 0) {
    return (
      <p className="text-sm text-muted">
        No attempt has been recorded yet. The platform files automatically once the document is
        issued.
      </p>
    );
  }

  return (
    <ol className="flex flex-col gap-0">
      {filings.map((filing, index) => {
        const failed = filing.status === "failed";
        const registered = filing.status === "filed";
        return (
          <li key={filing.id} className="relative flex gap-3 pb-4 last:pb-0">
            {/* The rule joining the attempts. Decorative — the order is the list's own. */}
            {index < filings.length - 1 ? (
              <span className="absolute left-[0.4375rem] top-5 bottom-0 w-px bg-line" aria-hidden />
            ) : null}
            <span
              className={cn(
                "mt-1 size-3.5 shrink-0 rounded-full ring-4 ring-surface",
                failed ? "bg-danger" : registered ? "bg-present" : "bg-info",
              )}
              aria-hidden
            />
            <div className="flex min-w-0 flex-col gap-0.5">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-sm font-bold text-ink">{filingRowLabel(filing)}</span>
                <time className="text-xs text-muted tnum" dateTime={filing.attempted_at}>
                  {formatDateTime(filing.attempted_at)}
                </time>
              </div>
              {filing.error ? (
                <p className="text-sm text-ink-soft">
                  {filing.error}
                  {filing.error_code ? (
                    <span className="ml-1.5 text-xs text-faint tnum">({filing.error_code})</span>
                  ) : null}
                </p>
              ) : null}
              {filing.ack_no ? (
                <p className="text-xs text-muted tnum">Acknowledgement {filing.ack_no}</p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The one correction that is still legal, behind a confirmation.
 *
 * Exactly one is ever offered: `cancel` while the portal's 24-hour window is open, `credit_note`
 * once it has closed. Offering both is how a user discovers which is right by having the other
 * fail, so the choice is made from the service's own `remedy` field and never from a clock read
 * here. The confirmation exists because both corrections are consequential and neither is undoable
 * by this screen.
 */
function RemedyCard({ invoice, state }: { invoice: Invoice; state: EInvoiceState }) {
  const navigate = useNavigate();
  const remedy = remedyView(state);
  // The `credit_note` remedy is a real write (`POST /finance/invoices`, gap 3) and goes straight
  // to that form — no local confirmation step here, since the form itself confirms before it
  // issues. `cancel` (IRN withdrawal) has no route on this platform today (`internal.py` only;
  // the tax-portal client is a stub) — it stays instructional, and never claims to have sent
  // anything.
  const [step, setStep] = useState<"closed" | "confirming" | "confirmed">("closed");
  const [reasonCode, setReasonCode] = useState("");

  if (remedy === null) return null;
  const deadline = state.cancellable_until ?? null;
  const reasonLabel =
    CANCELLATION_REASONS.find((r) => r.code === reasonCode)?.label ?? "";

  if (remedy.key === "contact_support") {
    return (
      <div className="flex flex-col gap-2 rounded-tile border border-line p-4">
        <h3 className="text-sm font-bold text-ink">{remedy.headline}</h3>
        <p className="max-w-prose text-sm text-muted">{remedy.consequence}</p>
      </div>
    );
  }

  if (remedy.key === "credit_note") {
    return (
      <div className="flex flex-col gap-3 rounded-tile border border-line p-4">
        <div className="flex flex-col gap-1">
          <h3 className="text-sm font-bold text-ink">{remedy.headline}</h3>
        </div>
        <p className="max-w-prose text-sm text-muted">{remedy.consequence}</p>
        <Can permission="finance.billing.invoice.issue" scope="ORG">
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => navigate(`/invoices/new?corrects=${invoice.id}`)}
          >
            {remedy.action}
          </Button>
        </Can>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-tile border border-line p-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-bold text-ink">{remedy.headline}</h3>
        {deadline ? (
          <p className="text-sm text-muted">
            Cancellable until{" "}
            <time className="font-semibold text-ink-soft tnum" dateTime={deadline}>
              {formatDateTime(deadline)}
            </time>
            .
          </p>
        ) : null}
      </div>

      {step === "closed" ? (
        <div className="flex flex-col gap-2">
          <p className="max-w-prose text-sm text-muted">{remedy.consequence}</p>
          <p className="text-xs text-muted">
            Not yet automated on this platform: withdrawal goes through the e-invoicing agent, not
            a button here. This walks through what to quote and where.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => setStep("confirming")}
          >
            {remedy.action}
          </Button>
        </div>
      ) : null}

      {step === "confirming" ? (
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            setStep("confirmed");
          }}
        >
          <p className="max-w-prose text-sm text-ink-soft">{remedy.consequence}</p>
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="cancel-reason"
              className="text-xs font-semibold uppercase tracking-wide text-faint"
            >
              Reason for withdrawal
            </label>
            <select
              id="cancel-reason"
              required
              value={reasonCode}
              onChange={(event) => setReasonCode(event.target.value)}
              className="h-11 max-w-xs rounded-tile bg-ground px-3 text-sm font-semibold text-ink focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
            >
              <option value="">Choose one…</option>
              {CANCELLATION_REASONS.map((reason) => (
                <option key={reason.code} value={reason.code}>
                  {reason.code} · {reason.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted">
              The portal takes one of these four and no free text.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={reasonCode === ""}>
              Show what to do next
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setStep("closed")}>
              Keep the registration
            </Button>
          </div>
        </form>
      ) : null}

      {step === "confirmed" ? (
        <div className="flex flex-col gap-2 rounded-tile bg-ground p-3" role="status">
          <p className="text-sm font-bold text-ink">What happens next</p>
          <p className="max-w-prose text-sm text-ink-soft">
            Withdrawals are filed with the portal by the platform's e-invoicing agent, not from
            this screen. Quote the IRN below under reason {reasonCode} ({reasonLabel})
            {deadline ? <> before {formatDateTime(deadline)}</> : null}.
          </p>
          {state.irn ? <CopyButton value={state.irn} label="Copy IRN" /> : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="self-start"
            onClick={() => setStep("closed")}
          >
            Done
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** The banner a terminal failure gets. Says it has stopped, why, and after how many attempts. */
function TerminalBanner({ state }: { state: EInvoiceState }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-tile bg-danger-soft px-4 py-3 text-danger"
    >
      <AlertOctagonIcon className="mt-0.5 size-5 shrink-0" />
      <div className="flex flex-col gap-1">
        <span className="text-sm font-bold">Filing has stopped. This one needs a person.</span>
        <span className="max-w-prose text-sm text-danger/90">
          {filingErrorSentence(state) ?? "The portal refused this document."}
        </span>
        <span className="text-xs text-danger/80 tnum">
          {state.attempts} {state.attempts === 1 ? "attempt" : "attempts"}
          {state.attempted_at ? <> · last at {formatDateTime(state.attempted_at)}</> : null}
        </span>
      </div>
    </div>
  );
}

/** Where the platform has got to on its own, for every state that is not terminal. */
function ProgressNote({ state }: { state: EInvoiceState }) {
  const view = filingView(state);
  if (view === null || view.needsPerson) return null;
  const registered = view.key === "filed";
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-tile px-4 py-3",
        registered ? "bg-present-soft text-present" : "bg-ground text-ink-soft",
      )}
    >
      {registered ? (
        <CheckCircleIcon className="mt-0.5 size-5 shrink-0" />
      ) : (
        <ClockIcon className="mt-0.5 size-5 shrink-0 text-muted" />
      )}
      <p className="max-w-prose text-sm font-medium">{view.detail}</p>
    </div>
  );
}

/**
 * The compliance panel on an invoice: what this document is under GST, where it stands with the
 * portal, every attempt made on it, and the one correction still open.
 */
export function CompliancePanel({ invoice }: { invoice: Invoice }) {
  const loader = useCallback(() => listFilings(invoice.id), [invoice.id]);
  const filings = useAsync(loader, [invoice.id]);
  const state = invoice.einvoice;
  const view = filingView(state);

  const registeredFiling =
    filings.status === "success"
      ? (filings.data.find((f) => f.status === "filed" && f.qr_payload !== null) ?? null)
      : null;

  return (
    <Card className="flex flex-col gap-6 p-6 lg:col-span-3 lg:p-8">
      <SectionHeader
        title="GST & e-invoice"
        subtitle="What this document declares, and where it stands with the Invoice Registration Portal"
        action={
          <span className="flex items-center gap-2">
            <SampleDataChip />
            <FilingStatusPill state={state} />
          </span>
        }
      />

      {/* What the document declares. Snapshots, not lookups: a filed invoice keeps naming the
          numbers it was raised under after either party re-registers. */}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 border-y border-line py-5 sm:grid-cols-3 lg:grid-cols-5">
        <Fact label="Seller GSTIN" value={invoice.seller_gstin ?? "Not recorded"} mono />
        <Fact label="Buyer GSTIN" value={invoice.buyer_gstin ?? "Unregistered (URP)"} mono />
        <Fact label="Place of supply" value={invoice.place_of_supply ?? "Not recorded"} mono />
        <Fact
          label="Supply type"
          value={SUPPLY_TYPE_LABEL[invoice.supply_type ?? "b2b"] ?? (invoice.supply_type ?? "—")}
        />
        <Fact label="Reverse charge" value={invoice.reverse_charge ? "Yes" : "No"} />
      </dl>

      <div className="flex flex-wrap items-center gap-2">
        {(invoice.igst_minor ?? 0) !== 0 ? (
          <Tag>IGST {formatMoney(invoice.igst_minor ?? 0)}</Tag>
        ) : (
          <>
            <Tag>CGST {formatMoney(invoice.cgst_minor ?? 0)}</Tag>
            <Tag>SGST {formatMoney(invoice.sgst_minor ?? 0)}</Tag>
          </>
        )}
        <span className="text-xs text-muted">
          {(invoice.igst_minor ?? 0) !== 0
            ? "Inter-state supply — the whole tax is levied as IGST."
            : "Intra-state supply — the tax is levied half as CGST and half as SGST."}
        </span>
      </div>

      {state === undefined ? (
        <p className="text-sm text-muted">
          This service did not report a filing position for this document, so nothing is claimed
          about it either way.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_auto]">
          <div className="flex flex-col gap-5">
            {view?.needsPerson ? <TerminalBanner state={state} /> : <ProgressNote state={state} />}

            {state.irn ? (
              <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="sm:col-span-2 flex flex-col gap-1">
                  <dt className="text-xs font-semibold uppercase tracking-wide text-faint">
                    Invoice Reference Number
                  </dt>
                  <dd className="money break-all text-sm leading-relaxed text-ink">{state.irn}</dd>
                  <dd className="pt-1">
                    <CopyButton value={state.irn} label="Copy IRN" />
                  </dd>
                </div>
                <Fact label="Acknowledgement no." value={state.ack_no ?? "—"} mono />
                <Fact
                  label="Acknowledged"
                  value={state.ack_date ? formatDateTime(state.ack_date) : "—"}
                  mono
                />
              </dl>
            ) : null}

            <RemedyCard invoice={invoice} state={state} />

            <div className="flex flex-col gap-3">
              <h3 className="text-sm font-bold text-ink">Attempts</h3>
              {filings.status === "loading" ? (
                <div className="flex flex-col gap-2">
                  {Array.from({ length: 2 }).map((_, i) => (
                    <Skeleton key={i} className="h-10 w-full rounded-tile" />
                  ))}
                </div>
              ) : null}
              {filings.status === "error" ? (
                <ErrorState message={readErrorMessage(filings.error, "the filing history")} />
              ) : null}
              {filings.status === "success" ? <FilingTimeline filings={filings.data} /> : null}
            </div>
          </div>

          {registeredFiling?.qr_payload ? (
            <SignedQr payload={registeredFiling.qr_payload} number={invoice.number} />
          ) : null}
        </div>
      )}
    </Card>
  );
}
