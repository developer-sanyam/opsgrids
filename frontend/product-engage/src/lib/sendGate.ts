import { ApiError } from "@opsgrids/client";

import { DASHBOARD_PRODUCTS_URL } from "@/lib/dashboard";
import { isNotActivated } from "@/lib/errors";

/**
 * Why a campaign was refused, and what to do about it.
 *
 * `POST /engage/campaigns/{id}/send` is the gate (`services.request_send`): a `402` when Engage is
 * not active, a `403` when the caller lacks the send permission, a `409` when the campaign is not a
 * draft any more, and a `422` naming exactly one unmet precondition in a sentence written to be
 * shown. Those sentences are the service's and are always shown **verbatim** as `message`.
 *
 * What this file adds is the *next step*. The `422` carries no machine code — only prose — so the
 * link or field to jump to is chosen by matching that prose. That is best-effort on purpose: if the
 * service rewords a sentence the match misses, `next` is `null`, and the customer is left with the
 * service's own explanation and no shortcut — never with a wrong shortcut, and never with a
 * paraphrase in place of the reason.
 *
 * **Two tests hold the two halves of that match, and neither alone would catch a reword.**
 * `sendGate.test.ts` pins what this file does with each sentence — but the sentences in it are
 * copies, so it cannot notice the service changing one. The service's side is
 * `services/notification/tests/test_engage_send_gate_sentences.py`, which raises every gate from
 * the real `request_send` and `_check_public_base` and pins the exact substring this file keys on.
 * A reword now fails a backend test, in the same change that made it, rather than silently
 * dropping a shortcut in production.
 *
 * The long-term fix is not a better match. It is a machine `code` on the `422` problem document
 * (`unusable_sender`, `domain_unverified`, `no_connection`, …) that this file switches on and the
 * prose stops being load-bearing — see the follow-up recorded in
 * `docs/backlog.md` (Known gaps not yet scheduled).
 */

/** A control on the campaign form a gate reason is about — the editor moves focus to it. */
export type GateField = "subject" | "body" | "sender" | "list";

export type GateNext =
  | { kind: "field"; label: string; field: GateField }
  | { kind: "link"; label: string; to: string; external?: boolean };

export interface SendGateReason {
  /** `warning` for a precondition the customer can fix (a `422`), `danger` for everything else. */
  tone: "warning" | "danger";
  title: string;
  /** The service's own sentence, verbatim — or, for a request that never reached the service, the
   *  one thing this app can truthfully say. */
  message: string;
  /** Who can fix it, when that is not the person looking at the screen. */
  hint: string | null;
  next: GateNext | null;
}

const OPERATOR_HINT =
  "This is a setting of the OpsGrids platform, not something in your organization. Contact OpsGrids support.";

function nextFor(message: string): { next: GateNext | null; hint: string | null } {
  const text = message.toLowerCase();
  if (text.includes("add a subject")) {
    return { next: { kind: "field", label: "Add a subject", field: "subject" }, hint: null };
  }
  if (text.includes("add a body")) {
    return { next: { kind: "field", label: "Write the body", field: "body" }, hint: null };
  }
  if (text.includes("sender no longer exists")) {
    return { next: { kind: "field", label: "Choose another sender", field: "sender" }, hint: null };
  }
  if (text.includes("has not passed a test send")) {
    return { next: { kind: "link", label: "Open Senders", to: "/senders" }, hint: null };
  }
  if (text.includes("no longer verified")) {
    return { next: { kind: "link", label: "Open Domains", to: "/domains" }, hint: null };
  }
  if (text.includes("connect an email provider")) {
    return { next: { kind: "link", label: "Open Connections", to: "/connection" }, hint: null };
  }
  if (text.includes("list no longer exists")) {
    return { next: { kind: "field", label: "Choose another list", field: "list" }, hint: null };
  }
  if (text.includes("unsubscribe links are not configured") || text.includes("engage_public_api_base")) {
    return { next: null, hint: OPERATOR_HINT };
  }
  return { next: null, hint: null };
}

export function explainSendFailure(error: unknown): SendGateReason {
  if (!(error instanceof ApiError)) {
    return {
      tone: "danger",
      title: "The campaign wasn't sent",
      message: "Couldn't reach the server. The campaign has not been sent — check your connection and try again.",
      hint: null,
      next: null,
    };
  }
  if (isNotActivated(error)) {
    return {
      tone: "danger",
      title: "Engage isn't active for this organization",
      message:
        "Campaigns can only be sent while Engage is active. Someone who manages your organization's products can start it from Products in the OpsGrids dashboard.",
      hint: null,
      next: { kind: "link", label: "Open Products in the dashboard", to: DASHBOARD_PRODUCTS_URL, external: true },
    };
  }
  if (error.status === 403) {
    return {
      tone: "danger",
      title: "You can't send campaigns",
      message: error.message,
      hint: "Sending needs its own permission, separate from drafting. Ask an Engage administrator to grant you “Send a campaign”.",
      next: null,
    };
  }
  if (error.status === 409) {
    return {
      tone: "danger",
      title: "This campaign can't be sent again",
      message: error.message,
      hint: null,
      next: null,
    };
  }
  if (error.status === 422) {
    const { next, hint } = nextFor(error.message);
    return { tone: "warning", title: "Not ready to send", message: error.message, hint, next };
  }
  return {
    tone: "danger",
    title: "The campaign wasn't sent",
    message: error.message,
    hint: null,
    next: null,
  };
}
