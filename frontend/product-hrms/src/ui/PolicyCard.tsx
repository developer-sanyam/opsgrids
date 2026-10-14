import { useState } from "react";

import { ApiError } from "@opsgrids/client";
import { Button, Card, InlineAlert, SectionHeader, Select, Switch, TextField } from "@opsgrids/ui";

import { updatePolicy } from "@/api/approvals";
import type { PolicyOut } from "@/api/types";
import { requestTypeCopy } from "@/lib/approvalCopy";
import { fieldError } from "@/lib/problem";

const MINUTES_PER_HOUR = 60;
const MINUTES_PER_DAY = 1440;
//: `ApprovalPolicy`'s own CHECK (`ck_approval_policies_sla_minutes_range`) — 15 minutes to 365
//: days (`services/approval/app/schemas.py::PolicyUpdateIn`). Mirrored here so the field can be
//: guarded before a round trip; the server enforces the same bound regardless (§2.5, §3.6).
const FLOOR_MINUTES = 15;
const CEILING_MINUTES = 525_600;

type Unit = "hours" | "days";

/** `1440 -> { amount: "1", unit: "days" }` — "expressed in the largest whole unit" (§4.G). Any
 *  value not an exact number of days is read in hours, which always divides evenly since every
 *  value this screen or the server can produce is a whole number of minutes. */
function unitFromMinutes(minutes: number): { amount: string; unit: Unit } {
  if (minutes % MINUTES_PER_DAY === 0) {
    return { amount: String(minutes / MINUTES_PER_DAY), unit: "days" };
  }
  return { amount: String(minutes / MINUTES_PER_HOUR), unit: "hours" };
}

/** The reverse of `unitFromMinutes`, from whatever the amount field currently holds. `null` for
 *  anything that isn't a positive number — the field's own basic legibility check, never a
 *  narrower business rule than the server's. */
function minutesFor(amount: string, unit: Unit): number | null {
  const value = Number(amount);
  if (!Number.isFinite(value) || value <= 0) return null;
  return Math.round(value * (unit === "days" ? MINUTES_PER_DAY : MINUTES_PER_HOUR));
}

function minutesLabel(minutes: number): string {
  const { amount, unit } = unitFromMinutes(minutes);
  const singular = unit === "days" ? "day" : "hour";
  return `${amount} ${amount === "1" ? singular : `${singular}s`}`;
}

interface PolicyCardProps {
  policy: PolicyOut;
  /** §2.4: a courtesy, not enforcement — the server refuses the `PUT` regardless of what this
   *  screen shows (`core.approval.policy.write:ORG`, `services/approval/app/api/routes.py`). */
  canEdit: boolean;
  announce: (message: string) => void;
}

/**
 * §4.G — one request type's SLA card. Today there is exactly one (`hrms.time.regularization`),
 * but nothing here assumes that: `ApprovalSettings` renders one of these per row `GET /policies`
 * returns.
 */
export function PolicyCard({ policy, canEdit, announce }: PolicyCardProps) {
  const copy = requestTypeCopy(policy.request_type);
  const initial = unitFromMinutes(policy.sla_minutes);

  const [committed, setCommitted] = useState({
    amount: initial.amount,
    unit: initial.unit,
    enabled: policy.enabled,
  });
  const [amount, setAmount] = useState(committed.amount);
  const [unit, setUnit] = useState<Unit>(committed.unit);
  const [enabled, setEnabled] = useState(committed.enabled);
  const [confirmingOff, setConfirmingOff] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [slaError, setSlaError] = useState<string | undefined>(undefined);
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [staleNote, setStaleNote] = useState<string | undefined>(undefined);

  const dirty = amount !== committed.amount || unit !== committed.unit || enabled !== committed.enabled;
  const canSave = canEdit && dirty && !confirmingOff && !submitting;
  // A missing row reads back at the server's own defaults (`domain.DEFAULT_SLA_MINUTES` = 1440,
  // `DEFAULT_POLICY_ENABLED` = true) — `GET /policies` never tells the client whether a row was
  // ever written, so this is the same value either way, and the note is true regardless of which
  // it is (§4.G's "Empty" state).
  const usingDefaults = policy.sla_minutes === MINUTES_PER_DAY && policy.enabled;

  function clearFeedback() {
    setSlaError(undefined);
    setFormError(undefined);
    setStaleNote(undefined);
  }

  function onAmountChange(value: string) {
    setAmount(value);
    clearFeedback();
  }

  function onUnitChange(value: Unit) {
    setUnit(value);
    clearFeedback();
  }

  function onToggle(next: boolean) {
    setEnabled(next);
    setConfirmingOff(!next);
    clearFeedback();
  }

  function discard() {
    setAmount(committed.amount);
    setUnit(committed.unit);
    setEnabled(committed.enabled);
    setConfirmingOff(false);
    clearFeedback();
  }

  async function save() {
    if (submitting) return;
    clearFeedback();

    if (!navigator.onLine) {
      setFormError("You're offline. Reconnect to save.");
      return;
    }

    const minutes = minutesFor(amount, unit);
    if (minutes === null) {
      setSlaError("Enter a number greater than zero.");
      return;
    }
    // Mirrors the server's own bound (§3.6), so an obviously out-of-range value never makes a
    // round trip — the server's field error (rendered below) is what still runs if this client
    // check is ever wrong or bypassed.
    if (minutes < FLOOR_MINUTES) {
      setSlaError("Give approvers at least 15 minutes.");
      return;
    }
    if (minutes > CEILING_MINUTES) {
      setSlaError("That's more than a year. Pick a shorter time.");
      return;
    }

    setSubmitting(true);
    try {
      const saved = await updatePolicy(policy.request_type, { sla_minutes: minutes, enabled });
      const nextBaseline = unitFromMinutes(saved.sla_minutes);
      setCommitted({ amount: nextBaseline.amount, unit: nextBaseline.unit, enabled: saved.enabled });
      setAmount(nextBaseline.amount);
      setUnit(nextBaseline.unit);
      setEnabled(saved.enabled);
      setConfirmingOff(false);
      if (saved.sla_minutes !== minutes || saved.enabled !== enabled) {
        const note = "Someone else changed this at the same time. These are the settings now.";
        setStaleNote(note);
        announce(note);
      } else {
        announce("Approval settings saved.");
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422) {
          const message = fieldError(err.problem, "sla_minutes");
          if (message) {
            setSlaError(message);
          } else {
            setFormError(err.problem?.detail ?? err.message);
          }
          return;
        }
        setFormError(err.problem?.detail ?? err.message);
        return;
      }
      setFormError("Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="p-6 flex flex-col gap-5">
      <SectionHeader title={copy.title} {...(copy.subtitle ? { subtitle: copy.subtitle } : {})} />

      {usingDefaults ? <p className="text-xs text-muted">Using the default settings.</p> : null}

      {canEdit ? (
        <>
          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-semibold uppercase tracking-wide text-faint">
              Time to decide
            </legend>
            <div className="flex flex-wrap items-start gap-3">
              <TextField
                label="Amount"
                type="number"
                min="0"
                step="any"
                value={amount}
                onChange={(e) => onAmountChange(e.target.value)}
                containerClassName="w-32"
                helper="How long each approver gets before the request moves up to their manager. Default: 1 day."
                {...(slaError !== undefined ? { error: slaError } : {})}
              />
              <Select
                label="Unit"
                value={unit}
                onChange={(e) => onUnitChange(e.target.value as Unit)}
                options={[
                  { value: "hours", label: "Hours" },
                  { value: "days", label: "Days" },
                ]}
                containerClassName="w-32"
              />
            </div>
          </fieldset>

          <Switch
            checked={enabled}
            onChange={onToggle}
            label="Move requests up when nobody answers"
            description={
              <span aria-live="polite">
                {enabled
                  ? "If an approver doesn't answer in time, the request moves up to their manager. If there's nobody above, it goes to HR."
                  : "Requests stay with the approver until they answer. They never approve themselves, and nothing is decided automatically — a request with escalation off can wait indefinitely."}
              </span>
            }
          />

          {confirmingOff ? (
            <InlineAlert
              tone="warning"
              title="Turn escalation off? Requests will wait for their approver with no time limit."
              action={
                <div className="flex gap-2">
                  <Button variant="dark" size="sm" onClick={() => setConfirmingOff(false)}>
                    Turn it off
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setEnabled(true);
                      setConfirmingOff(false);
                    }}
                  >
                    Keep escalation on
                  </Button>
                </div>
              }
            />
          ) : null}

          <p className="text-xs text-muted">
            Changes apply to new requests. Anything already waiting keeps the due time it was given.
          </p>

          {staleNote ? <InlineAlert tone="warning" title={staleNote} /> : null}
          {formError ? <InlineAlert tone="danger" title={formError} /> : null}

          <div className="flex items-center gap-3">
            <Button variant="primary" onClick={() => void save()} disabled={!canSave}>
              {submitting ? "Saving…" : "Save changes"}
            </Button>
            {dirty ? (
              <Button variant="ghost" onClick={discard} disabled={submitting}>
                Discard
              </Button>
            ) : null}
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-2 text-sm">
          <p>
            <span className="text-muted">Time to decide: </span>
            <span className="font-semibold text-ink">{minutesLabel(policy.sla_minutes)}</span>
          </p>
          <p>
            <span className="text-muted">Requests move up when nobody answers: </span>
            <span className="font-semibold text-ink">{policy.enabled ? "Yes" : "No"}</span>
          </p>
        </div>
      )}
    </Card>
  );
}
