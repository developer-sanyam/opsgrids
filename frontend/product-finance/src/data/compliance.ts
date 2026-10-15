/**
 * The compliance vocabulary: how a filing position and a reconciliation suggestion are *said*.
 *
 * Pure functions, no I/O, no React — the same sentence appears on the invoice list, the invoice
 * detail and the overview because all three call in here rather than each writing their own.
 *
 * Two rules shape everything below.
 *
 * **A wire value is not a sentence.** `amount_mismatch` and `+124000` are what the service stores;
 * "₹1,240 more than your purchase record" is what a person can act on. Translating at the edge —
 * once, here — is what keeps the screens readable and the contract stable.
 *
 * **A state that needs a person must say so.** A terminal filing failure that reads as "pending"
 * is a missed statutory deadline dressed as a spinner, so `needsPerson` is carried explicitly and
 * comes from the service's own `terminal` flag rather than from a guess about status strings.
 */

import type {
  EInvoiceFiling,
  EInvoiceState,
  EwayStatus,
  FilingRemedy,
  ImsAction,
  ImsReason,
  ImsStatus,
  ImsSuggestion,
  Invoice,
} from "@/api/types";
import { formatMoney } from "@/lib/money";

/* ------------------------------------------------------------------ e-invoice filing */

/**
 * The six positions a document can occupy with the portal, as a screen has to distinguish them.
 * Wider than the wire's four statuses because `required` and `terminal` split two of them:
 * "no IRN needed" and "not filed yet" are both an absent status, and they mean opposite things.
 */
export type FilingStateKey =
  | "not_required"
  | "not_filed"
  | "in_flight"
  | "filed"
  | "failed"
  | "cancelled";

export interface FilingView {
  key: FilingStateKey;
  /** The short label a pill or a table column carries. Never colour alone. */
  label: string;
  /** One sentence: what this means, and where it applies what a person still has to do. */
  detail: string;
  /** True once the platform has stopped on its own. Drives every "needs attention" surface. */
  needsPerson: boolean;
}

/** Error codes this *platform* writes, as opposed to the numbered ones the portal returns. */
const PLATFORM_ERRORS: Record<string, string> = {
  retry_exhausted:
    "The portal never answered and the platform has spent its retry budget. Nothing further happens on its own.",
  unavailable: "The portal did not answer.",
  no_registration:
    "This organization has no GST registration on file, so there is nothing to file under.",
};

/**
 * The portal's message, or ours, as a sentence.
 *
 * A numbered code is the portal's and is shown *with* its number, because that number is what a
 * support conversation with NIC turns on. A named code is ours and is spelled out — nobody outside
 * this codebase should have to know what `retry_exhausted` means.
 */
export function filingErrorSentence(state: EInvoiceState): string | null {
  const code = state.error_code ?? null;
  if (code === null && !state.error) return null;
  if (code !== null) {
    const platform = PLATFORM_ERRORS[code];
    if (platform !== undefined) return platform;
    return state.error ? `${state.error} (portal code ${code})` : `Portal code ${code}.`;
  }
  return state.error ?? null;
}

/**
 * Where a document stands with the portal, in words.
 *
 * Returns `null` when the service reported no filing position at all — an older build, or a
 * response that predates the compliance slice. That is a real state and it is left blank rather
 * than guessed at: claiming "not filed" about a document nobody asked about would be a fabricated
 * obligation, and claiming "filed" would be worse.
 */
export function filingView(state: EInvoiceState | undefined): FilingView | null {
  if (state === undefined) return null;

  if (!state.required) {
    return {
      key: "not_required",
      label: "No IRN needed",
      detail: "This document does not have to be registered with the portal.",
      needsPerson: false,
    };
  }

  switch (state.status) {
    case null:
    case undefined:
      return {
        key: "not_filed",
        label: "Not filed",
        detail: "Registration is due. The platform files this automatically.",
        needsPerson: false,
      };
    case "pending":
      return {
        key: "in_flight",
        label: "Filing",
        detail:
          state.attempts > 1
            ? `Attempt ${state.attempts} is with the portal. The platform retries on its own.`
            : "With the portal now. The platform retries on its own.",
        needsPerson: false,
      };
    case "filed":
      return {
        key: "filed",
        label: "Filed (simulated)",
        detail: "Filed with the simulator. The invoice carries the IRN and signed QR the filing returned.",
        needsPerson: false,
      };
    case "cancelled":
      return {
        key: "cancelled",
        label: "IRN cancelled",
        detail:
          "The registration was withdrawn at the portal. No IRN stands against this document.",
        needsPerson: false,
      };
    case "cancelling":
      return {
        key: "in_flight",
        label: "Cancelling IRN",
        detail:
          "Withdrawal requested; the platform finishes it if the portal was unreachable.",
        needsPerson: false,
      };
    case "failed":
      if (state.ack_date) {
        // A failed *withdrawal*: the row carries the acknowledgement it was withdrawing, so the
        // registration still stands. Not "filing failed" — the document is filed.
        return {
          key: "failed",
          label: "Withdrawal failed",
          detail: `${filingErrorSentence(state) ?? "The portal did not confirm the withdrawal."} The IRN still stands.`,
          needsPerson: true,
        };
      }
      return {
        key: "failed",
        label: "Filing failed",
        detail:
          filingErrorSentence(state) ??
          "The portal refused this document and the platform has stopped trying.",
        needsPerson: state.terminal ?? true,
      };
    default:
      return {
        key: "failed",
        label: "Filing failed",
        detail:
          filingErrorSentence(state) ??
          "The portal refused this document and the platform has stopped trying.",
        needsPerson: state.terminal ?? true,
      };
  }
}

/**
 * One row of a document's filing history, as a heading. A `cancelling` row is a withdrawal
 * requested and not yet answered — never "Submitted", which is what a registration attempt awaiting
 * the portal is. A `failed` row carrying an acknowledgement is a withdrawal the portal refused
 * (the registration still stands), not a refused filing.
 */
export function filingRowLabel(filing: Pick<EInvoiceFiling, "status" | "ack_date">): string {
  switch (filing.status) {
    case "filed":
      return "Registered (simulated)";
    case "cancelled":
      return "Withdrawn";
    case "cancelling":
      return "Withdrawal requested";
    case "failed":
      return filing.ack_date ? "Withdrawal refused" : "Refused";
    default:
      return "Submitted";
  }
}

/* ------------------------------------------------------------------ e-Way bills */

/** The stored statuses a person can filter the e-Way register by, in the order they read. */
export const EWAY_STATUS_FILTERS: readonly { value: EwayStatus | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "submitting", label: "Being raised" },
  { value: "pending", label: "Pending" },
  { value: "generated", label: "Generated" },
  { value: "cancelling", label: "Being withdrawn" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

const EWAY_TONE: Record<EwayStatus, string> = {
  submitting: "bg-info-soft text-info",
  pending: "bg-late-soft text-late",
  generated: "bg-present-soft text-present",
  cancelling: "bg-info-soft text-info",
  failed: "bg-danger-soft text-danger",
  cancelled: "bg-ground text-muted",
  expired: "bg-ground text-muted",
};

/** The tag colour for a bill's effective status. An in-flight claim reads as work in progress,
 * never as a failure or as done. */
export function ewayStatusTone(status: string): string {
  return EWAY_TONE[status as EwayStatus] ?? "bg-ground text-muted";
}

/** The corrections — never offered together, because only one is ever legal at a time. */
export interface RemedyView {
  key: FilingRemedy;
  /** The control's label. Ends in an ellipsis: it opens a confirmation, it does not act. */
  action: string;
  headline: string;
  /** What this correction does, and what it cannot undo. */
  consequence: string;
}

export function remedyView(state: EInvoiceState | undefined): RemedyView | null {
  if (state?.remedy === "cancel") {
    return {
      key: "cancel",
      action: "Withdraw this IRN…",
      headline: "The registration can still be withdrawn",
      consequence:
        "Withdrawing cancels the registration at the portal. The document keeps its number and stays in the register — a cancellation is a new filing row, never an edit. Once the window closes the only correction left is a credit note.",
    };
  }
  if (state?.remedy === "contact_support") {
    return {
      key: "contact_support",
      action: "",
      headline: "The withdrawal could not be confirmed",
      consequence:
        "The platform asked the portal to withdraw this IRN and never saw an answer before the 24-hour window closed. The portal may already hold the withdrawal. Check the IRN on the portal: if it still stands, the correction is a credit note.",
    };
  }
  if (state?.remedy === "credit_note") {
    return {
      key: "credit_note",
      action: "Raise a credit note…",
      headline: "The cancellation window has closed",
      consequence:
        "This IRN can no longer be withdrawn. A credit note against this invoice is the correction that remains: it reverses the amount on a new document and leaves this one registered, which is what an append-only register means in practice.",
    };
  }
  return null;
}

/** The portal's four cancellation reasons. It takes no free-text alternative, so nor do we. */
export const CANCELLATION_REASONS: readonly { code: string; label: string }[] = [
  { code: "1", label: "Duplicate" },
  { code: "2", label: "Data entry mistake" },
  { code: "3", label: "Order cancelled" },
  { code: "4", label: "Others" },
];

/* ------------------------------------------------------------------ IMS reconciliation */

/** The reason as a filter chip reads it — two or three words, no wire value. */
const REASON_LABEL: Record<ImsReason, string> = {
  matched: "Exact match",
  amount_mismatch: "Amount differs",
  no_purchase_record: "No purchase record",
  supplier_unknown: "Unknown supplier",
  duplicate: "Duplicate",
};

export function reasonLabel(reason: ImsReason): string {
  return REASON_LABEL[reason];
}

/**
 * The suggestion as a sentence a person can act on without translating anything.
 *
 * The mismatch case is the one that earns this function: the service carries a signed number of
 * paise, and "1,24,000 paise" is not a fact anybody in an accounts office reasons about. Money
 * comes out through `formatMoney`, so it is rupees, grouped the Indian way, to the paisa.
 */
export function suggestionSentence(suggestion: ImsSuggestion): string {
  switch (suggestion.reason) {
    case "matched":
      return "Matches your purchase record exactly — taxable value and total alike.";
    case "amount_mismatch": {
      const direction = suggestion.delta_minor > 0 ? "more" : "less";
      return `${formatMoney(Math.abs(suggestion.delta_minor))} ${direction} than your purchase record.`;
    }
    case "no_purchase_record":
      return "A supplier you buy from, but nothing on file with this document number this financial year.";
    case "supplier_unknown":
      return "You have never purchased from this supplier.";
    case "duplicate":
      return "You already have this document. This is a second copy of it.";
  }
}

const ACTION_LABEL: Record<ImsAction, string> = {
  accept: "Accept",
  reject: "Reject",
  pending_action: "Hold",
};

export function actionLabel(action: ImsAction): string {
  return ACTION_LABEL[action];
}

/** The decision as a completed act, for a history line: "Priya Nair put this document on hold". */
const ACTION_PAST: Record<ImsAction, string> = {
  accept: "accepted this document",
  reject: "rejected this document",
  pending_action: "put this document on hold",
};

export function actionPastTense(action: ImsAction): string {
  return ACTION_PAST[action];
}

/** What each decision actually does to this organization's money. Shown on the decision controls. */
const ACTION_CONSEQUENCE: Record<ImsAction, string> = {
  accept: "Claims the input tax credit on this document.",
  reject: "Declines the credit and sends the document back to the supplier.",
  pending_action: "Parks it: real, but not settled this period.",
};

export function actionConsequence(action: ImsAction): string {
  return ACTION_CONSEQUENCE[action];
}

const STATUS_LABEL: Record<ImsStatus, string> = {
  pending: "Undecided",
  accepted: "Accepted",
  rejected: "Rejected",
  pending_action: "On hold",
};

export function imsStatusLabel(status: ImsStatus): string {
  return STATUS_LABEL[status];
}

/** Undecided and on-hold rows are the queue's real backlog; settled ones are history. */
export function isOutstanding(status: ImsStatus): boolean {
  return status === "pending" || status === "pending_action";
}

/* ---------------------------------------------------------------------- the rollup */

/**
 * The compliance position for one return period, plus everything terminal wherever it sits.
 *
 * The filing counts are period-scoped because a GST return is; the failures are not, because a
 * document that failed in July is still unfiled in August and is still somebody's problem.
 */
export interface ComplianceSummary {
  /** The return period these counts cover, `YYYY-MM`. */
  period: string;
  periodLabel: string;
  /** Documents in the period that must carry an IRN. */
  required: number;
  filed: number;
  /** Required, not yet registered, and not stuck — the automation is still working on these. */
  awaiting: number;
  /** Every document the platform has given up on, newest first. Needs a person. */
  terminal: Invoice[];
  /** Documents whose filing position the service did not report at all. */
  unreported: number;
}

/** `"2026-08"` → `"August 2026"`. Rendered in UTC so a stored period shows as-is. */
export function periodLabel(period: string): string {
  const parsed = new Date(`${period}-01T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return period;
  return parsed.toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
}

/** The newest return period the book has documents in — the one a person is working now. */
export function latestPeriod(invoices: readonly Invoice[]): string | null {
  let latest: string | null = null;
  for (const invoice of invoices) {
    if (invoice.status === "draft") continue; // a draft is not reportable
    const period = invoice.issued_date.slice(0, 7);
    if (latest === null || period > latest) latest = period;
  }
  return latest;
}

/** Roll a page of invoices into the compliance position for `period`. */
export function complianceSummary(
  invoices: readonly Invoice[],
  period: string,
): ComplianceSummary {
  let required = 0;
  let filed = 0;
  let awaiting = 0;
  let unreported = 0;
  const terminal: Invoice[] = [];

  for (const invoice of invoices) {
    const view = filingView(invoice.einvoice);
    if (view === null) {
      unreported += 1;
      continue;
    }
    if (view.needsPerson) terminal.push(invoice);

    if (invoice.issued_date.slice(0, 7) !== period || invoice.status === "draft") continue;
    if (view.key === "not_required") continue;
    required += 1;
    if (view.key === "filed") filed += 1;
    else if (view.key === "not_filed" || view.key === "in_flight") awaiting += 1;
  }

  terminal.sort((a, b) => (a.issued_date < b.issued_date ? 1 : -1));
  return { period, periodLabel: periodLabel(period), required, filed, awaiting, terminal, unreported };
}
