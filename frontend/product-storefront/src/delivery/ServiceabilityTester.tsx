import { useState } from "react";

import {
  Button,
  Card,
  InlineAlert,
  SectionHeader,
  Tag,
  TextField,
} from "@opsgrids/ui";

import { checkServiceability, classifyCheckFailure } from "./api";
import { answerPromiseSentence, feeLines, modeLabel, reasonLabel } from "./copy";
import { isValidPostalCode, normalisePostalCode } from "./postalCodes";
import type { CheckState, MerchantServiceabilityOut } from "./types";

/**
 * The tester: a postal code in, the real answer out.
 *
 * It calls `POST /delivery/check`, which runs **the same selector** as the buyer's public route
 * (ADR-0010 §5, §9) and returns the same shape plus the matched zone id. It has no rules of its
 * own and computes nothing — that is the entire point. A tester with its own code path tests
 * itself.
 *
 * **Unknown is not no** (ADR-0010 §13). A network failure, a rate limit or a 5xx renders as
 * "couldn't check", never as "not serviceable". A merchant who reads a transient failure as "my own
 * store won't deliver to my own warehouse" files a bug that costs a day, and the demand report
 * would be the only thing that eventually contradicted it.
 */
/**
 * No `store` prop: every fact on the card — the currency, the promise, the fee caveat — arrives on
 * the answer itself. Reaching for the store document to fill in a blank would be the client
 * computing part of an answer the server already gave.
 */
export function ServiceabilityTester() {
  const [input, setInput] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [state, setState] = useState<CheckState>({ status: "idle" });
  const [denied, setDenied] = useState<string | null>(null);

  async function run(raw: string) {
    if (state.status === "checking") return;
    const postalCode = normalisePostalCode(raw);

    // Client validation mirrors the server's rule; it never replaces it. This one only saves a
    // round trip on an obviously empty or malformed box.
    if (postalCode === "") {
      setFieldError("Type a postal code to check.");
      return;
    }
    if (!isValidPostalCode(postalCode)) {
      setFieldError("Indian PIN codes are six digits, like 400050.");
      return;
    }

    setFieldError(null);
    setDenied(null);
    setState({ status: "checking", postal_code: postalCode });
    try {
      const answer = await checkServiceability(postalCode);
      setState({ status: "answered", answer });
    } catch (error) {
      const failure = classifyCheckFailure(error);
      if (failure.kind === "invalid") {
        setFieldError(failure.detail);
        setState({ status: "idle" });
        return;
      }
      if (failure.kind === "denied" || failure.kind === "plan") {
        setDenied(failure.detail);
        setState({ status: "idle" });
        return;
      }
      setState({
        status: "unknown",
        postal_code: postalCode,
        detail: failure.detail,
        reference: failure.reference,
      });
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-4 p-5 sm:p-6">
        <SectionHeader
          title="Check a postal code"
          subtitle="Goes through the same endpoint a buyer's check does, so what you see here is what they get."
        />

        <form
          className="flex flex-col gap-3 sm:flex-row sm:items-start"
          onSubmit={(event) => {
            event.preventDefault();
            void run(input);
          }}
        >
          <TextField
            label="Postal code"
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
              if (fieldError !== null) setFieldError(null);
            }}
            inputMode="numeric"
            autoComplete="postal-code"
            placeholder="400050"
            className="tnum"
            containerClassName="sm:max-w-64"
            disabled={state.status === "checking"}
            {...(fieldError !== null ? { error: fieldError } : {})}
          />
          {/* Aligned to the field's control row rather than its label. `sm:mt-6` is the label's
              own height on the 4px grid, not a nudge. */}
          <Button
            type="submit"
            className="sm:mt-6"
            disabled={state.status === "checking"}
          >
            {state.status === "checking" ? "Checking…" : "Check"}
          </Button>
        </form>

        {denied !== null ? (
          <InlineAlert tone="warning" title="That check wasn't allowed">
            {denied}
          </InlineAlert>
        ) : null}
      </Card>

      {/* The answer replaces a spinner without a click, so it is announced. Polite, not assertive:
          the merchant asked for it and is looking at it. */}
      <div aria-live="polite">
        <AnswerCard state={state} onRetry={() => void run(input)} />
      </div>
    </div>
  );
}

/**
 * The four render states, one component.
 *
 * Reserving the card's height across all four is what stops the page jumping as a check resolves —
 * the answer lands where the spinner was.
 */
function AnswerCard({ state, onRetry }: { state: CheckState; onRetry: () => void }) {
  if (state.status === "idle") {
    return (
      <Card className="flex min-h-40 flex-col justify-center gap-2 p-5 text-center sm:p-6">
        <p className="text-sm font-semibold text-ink">No check run yet</p>
        <p className="mx-auto max-w-md text-sm text-muted">
          Type a postal code above. Try one you deliver to and one you don't — a zone map that looks
          right and answers wrong is the one failure nothing else here detects.
        </p>
      </Card>
    );
  }

  if (state.status === "checking") {
    return (
      <Card className="flex min-h-40 flex-col justify-center gap-2 p-5 text-center sm:p-6">
        <p className="text-sm font-semibold text-ink tnum">Checking {state.postal_code}…</p>
        <p className="text-sm text-muted">Asking the server the same question a buyer asks.</p>
      </Card>
    );
  }

  if (state.status === "unknown") {
    return (
      <Card className="flex min-h-40 flex-col justify-center gap-3 p-5 sm:p-6">
        {/* Never "not serviceable". The heading is the fact: we could not get an answer. */}
        <InlineAlert
          tone="warning"
          title={`Couldn't check ${state.postal_code}`}
          {...(state.reference !== null ? { reference: state.reference } : {})}
          action={
            <Button size="sm" variant="outline" onClick={onRetry}>
              Try again
            </Button>
          }
        >
          {state.detail} This is not an answer about your zones — it means the question never got
          through.
        </InlineAlert>
      </Card>
    );
  }

  return <Answer answer={state.answer} />;
}

function Answer({ answer }: { answer: MerchantServiceabilityOut }) {
  const served = answer.serviceable;
  const promise = answerPromiseSentence(answer.promise);
  const fees = feeLines(answer.fees);

  return (
    <Card className="flex min-h-40 flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-faint tnum">
            {answer.postal_code}
          </p>
          <h3 className="display text-xl font-extrabold">
            {served ? "Yes — you deliver here" : "No — you don't deliver here"}
          </h3>
          {promise !== null ? <p className="text-sm text-ink-soft">Buyers are told: {promise}</p> : null}
          {!served && answer.reason !== null ? (
            <p className="text-sm text-ink-soft">{reasonLabel(answer.reason)}</p>
          ) : null}
        </div>
        <Tag tone={served ? "positive" : "neutral"}>
          {answer.zone_id === null ? "No zone" : modeLabel(answer.mode)}
        </Tag>
      </div>

      {answer.zone_name !== null ? (
        <p className="text-sm text-muted">
          Matched zone: <span className="font-semibold text-ink">{answer.zone_name}</span>
        </p>
      ) : null}

      {answer.buyer_note !== null ? (
        <p className="text-sm text-ink-soft">“{answer.buyer_note}”</p>
      ) : null}

      {fees !== null ? (
        <div className="flex flex-col gap-1 border-t border-line pt-3">
          {/* ADR-0010 §8: display values until slice 4. The caveat is driven by the server's own
              `fees.display_only`, not hard-coded — so when the pricing engine starts applying
              these, the screen stops claiming it does not without a frontend change. */}
          <p className="text-xs font-semibold uppercase tracking-wide text-faint">
            {fees.displayOnly ? "Shown to buyers — not charged" : "Charged at checkout"}
          </p>
          {fees.fee !== null ? <p className="text-sm text-ink-soft">{fees.fee}</p> : null}
          {fees.minimum !== null ? <p className="text-sm text-ink-soft">{fees.minimum}</p> : null}
          {fees.displayOnly ? (
            <p className="text-xs text-muted">
              There is no cart or checkout yet, so nothing applies these to an order.
            </p>
          ) : null}
        </div>
      ) : null}

      {answer.reason === "no_mapping" ? (
        <p className="text-sm text-muted">
          This postal code is in no zone, so every check for it lands in your demand report.
        </p>
      ) : null}
    </Card>
  );
}
