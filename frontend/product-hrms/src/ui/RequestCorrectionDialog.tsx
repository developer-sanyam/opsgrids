import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApiError } from "@opsgrids/client";
import {
  Button,
  Dialog,
  InlineAlert,
  RadioGroup,
  TextArea,
  formatInZone,
  resolveZone,
  useAnnounce,
} from "@opsgrids/ui";
import type { ZoneLookup } from "@opsgrids/ui";

import { requestRegularization } from "@/api/client";
import type { AttendanceRecord } from "@/api/types";
import { attendanceStatusLabel } from "@/lib/format";
import { fieldError } from "@/lib/problem";

const REASON_MAX = 500;

interface RequestCorrectionDialogProps {
  open: boolean;
  onClose: () => void;
  record: AttendanceRecord;
  zoneLookup: ZoneLookup | null;
  /** Called after a successful submit, or after the server says the record is gone (404). */
  onSettled: (result: { kind: "created" } | { kind: "gone" }) => void;
}

/**
 * §4.C — the correction dialog. Every failure branch in §3.1 renders here: 409 (already
 * pending), 422 business-rule (illegal transition), 422 validation (reason), 404 (record gone),
 * offline. Nothing typed is ever cleared on failure.
 */
export function RequestCorrectionDialog({
  open,
  onClose,
  record,
  zoneLookup,
  onSettled,
}: RequestCorrectionDialogProps) {
  const [changeTo, setChangeTo] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [choiceError, setChoiceError] = useState<string | undefined>(undefined);
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [conflict, setConflict] = useState(false);
  const [offline, setOffline] = useState(false);
  const announce = useAnnounce();

  const fieldsetContainerRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLElement | null>(null);
  const reasonContainerRef = useRef<HTMLDivElement>(null);

  // The record's own punch location governs its day (§2.1, C9) — never the organization's zone
  // by default, and never the browser's.
  const zone = resolveZone(record.location_code, zoneLookup ?? { locations: {}, organizationZone: "UTC" }).zone;
  const dayLabel = (): string => formatInZone(`${record.work_date}T00:00:00Z`, zone, "date");

  useEffect(() => {
    if (!open) return;
    // Reset per-open, but never per-keystroke — nothing typed is lost while the dialog is up.
    setChangeTo(null);
    setReason("");
    setSubmitting(false);
    setChoiceError(undefined);
    setReasonError(undefined);
    setFormError(undefined);
    setConflict(false);
    setOffline(false);
  }, [open, record.id]);

  // A layout effect, not a passive one: `Dialog`'s own focus effect reads `initialFocusRef.current`
  // as soon as it mounts, and every layout effect in a commit fires before any passive effect,
  // regardless of which component is the "parent" — see `DecisionDialog` for the same fix.
  useLayoutEffect(() => {
    if (!open) return;
    firstFieldRef.current =
      fieldsetContainerRef.current?.querySelector<HTMLElement>('input[type="radio"]') ?? null;
  }, [open, record.id]);

  // A11y fix: a validation failure — client-side or the server's 422 — moved no focus and gave a
  // screen-reader user nothing to react to. The field's own `aria-describedby` already reads the
  // error once focus lands there (`FormField`/`RadioGroup`'s documented pattern — a per-field
  // `role="alert"` would interrupt on every one of several errors, which is why that component
  // deliberately does not carry one); the missing half was ever moving focus there at all.
  function focusChoice() {
    fieldsetContainerRef.current?.querySelector<HTMLInputElement>('input[type="radio"]')?.focus();
  }
  function focusReason() {
    reasonContainerRef.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
  }

  async function submit() {
    if (submitting) return;
    setChoiceError(undefined);
    setReasonError(undefined);
    setFormError(undefined);

    if (!navigator.onLine) {
      setOffline(true);
      return;
    }
    if (!changeTo) {
      setChoiceError("Choose what the day should say.");
      focusChoice();
      return;
    }
    if (!reason.trim()) {
      setReasonError("Reason cannot be blank");
      focusReason();
      return;
    }
    if (reason.length > REASON_MAX) {
      setReasonError(`Reason must be at most ${REASON_MAX} characters`);
      focusReason();
      return;
    }

    setSubmitting(true);
    try {
      const created = await requestRegularization(record.id, {
        requested_status: changeTo as AttendanceRecord["status"],
        reason,
      });
      // Match `CorrectionCell`'s own tri-state (§3.1, §4.C, O6) rather than always claiming the
      // request hasn't been routed yet: `approval_opened_at` is the same field the cell reads to
      // tell "not yet routed" apart from "routed" — and once routed, name the approver exactly
      // as the cell now does, never a blank or a guessed name (§4.C, "Success" state).
      announce(
        `Correction requested for ${dayLabel()}. ${
          created.approval_opened_at === null
            ? "Waiting to be routed."
            : created.approver_name
              ? `Waiting on ${created.approver_name}.`
              : "Correction pending."
        }`,
      );
      onSettled({ kind: "created" });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setConflict(true);
          return;
        }
        if (err.status === 404) {
          onSettled({ kind: "gone" });
          return;
        }
        if (err.status === 422) {
          const reasonMessage = fieldError(err.problem, "reason");
          if (reasonMessage) {
            setReasonError(reasonMessage);
            focusReason();
          } else {
            // A business-rule 422 (illegal transition) — rendered under the radio group,
            // verbatim from the server (§3.1).
            setChoiceError(err.problem?.detail ?? err.message);
            focusChoice();
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

  const options = record.allowed_transitions.map((status) => ({
    value: status,
    label: attendanceStatusLabel(status),
  }));

  const contextLine = `${dayLabel()} · currently ${attendanceStatusLabel(record.status)}`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Request a correction"
      description={contextLine}
      initialFocusRef={firstFieldRef}
      footer={
        conflict ? (
          <Button variant="dark" onClick={onClose}>
            Close
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void submit()} disabled={submitting}>
              {submitting ? "Sending…" : "Send request"}
            </Button>
          </>
        )
      }
    >
      {conflict ? (
        <InlineAlert tone="warning" title="There's already a correction waiting on this day." />
      ) : (
        <div className="flex flex-col gap-4">
          <div ref={fieldsetContainerRef}>
            <RadioGroup
              legend="Change to"
              name="change-to"
              value={changeTo}
              onChange={setChangeTo}
              options={options}
              helper="Pick what the day should say."
              {...(choiceError !== undefined ? { error: choiceError } : {})}
            />
          </div>
          <div ref={reasonContainerRef}>
            <TextArea
              label="Why does this need changing?"
              helper='Your approver sees this. For example: "Worked from the Pune office, forgot to punch in."'
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={REASON_MAX}
              counterFrom={400}
              {...(reasonError !== undefined ? { error: reasonError } : {})}
            />
          </div>
          {offline ? (
            <InlineAlert tone="warning" title="You're offline. Reconnect to send this request." />
          ) : null}
          {formError ? <InlineAlert tone="danger" title={formError} /> : null}
          <p className="text-xs text-muted">This goes to your reporting manager for approval.</p>
        </div>
      )}
    </Dialog>
  );
}
