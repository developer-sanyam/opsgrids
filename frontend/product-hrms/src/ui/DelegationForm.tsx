import { useEffect, useState } from "react";

import { ApiError } from "@opsgrids/client";
import {
  Button,
  Card,
  DateRangeField,
  InlineAlert,
  SectionHeader,
  Skeleton,
  formatInZone,
  instantToWallClock,
  resolveZone,
  wallClockToInstant,
} from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

import { createDelegation } from "@/api/approvals";
import { getEmployee } from "@/api/client";
import type { DelegationCreateOut } from "@/api/types";
import { delegationConsequenceNote } from "@/lib/delegationCopy";
import { fieldError } from "@/lib/problem";
import type { ZoneDirectory } from "@/lib/zone";
import { delegationZoneNote } from "@/lib/zone";
import { DelegatePicker } from "@/ui/DelegatePicker";

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

/**
 * The instant one wall-clock hour after `now`, in `zone` — §4.E's "Starts … Defaults to the next
 * hour boundary". Field-by-field rather than adding milliseconds: an hour is added to the *wall
 * clock*, and letting `Date`'s own UTC arithmetic roll the day, month or year over is simpler and
 * safer than reimplementing a calendar, so `Date.UTC` is used here purely as calendar arithmetic
 * on civil fields — the actual zone conversion happens once, in `wallClockToInstant` at the end.
 */
function nextHourBoundary(zone: string, now: Date = new Date()): string {
  const wall = instantToWallClock(now, zone);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(wall);
  if (!match) return wallClockToInstant(wall, zone);
  const [, y, mo, d, h] = match as unknown as [string, string, string, string, string];
  const rolled = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h) + 1, 0, 0));
  const nextWall = `${rolled.getUTCFullYear()}-${pad(rolled.getUTCMonth() + 1)}-${pad(rolled.getUTCDate())}T${pad(rolled.getUTCHours())}:${pad(rolled.getUTCMinutes())}`;
  return wallClockToInstant(nextWall, zone);
}

interface DelegationFormProps {
  /** Whether the caller holds `core.approval.delegation.write:ORG` — the only scope that reaches
   *  the "Delegating for" field (§4.E, §2.12: TEAM and LOCATION collapse to OWN below it). */
  orgMode: boolean;
  myEmployeeId: string | null;
  zoneLookup: ZoneDirectory | null;
  onCreated: (created: DelegationCreateOut, forSelf: boolean, delegatorName: string | null) => void;
  onCancel: () => void;
}

/**
 * §4.E's create form — inline above the list, not a dialog: the date range needs room and the
 * list is useful context while choosing. Every failure branch in §3.3 renders here: the two 422s
 * (self-delegation, a delegate who has left), 409 (overlap), client-side window validation, and
 * offline.
 */
export function DelegationForm({
  orgMode,
  myEmployeeId,
  zoneLookup,
  onCreated,
  onCancel,
}: DelegationFormProps) {
  const [delegatingFor, setDelegatingFor] = useState<ComboboxOption | null>(null);
  const [delegate, setDelegate] = useState<ComboboxOption | null>(null);
  const [start, setStart] = useState<string | null>(null);
  const [end, setEnd] = useState<string | null>(null);
  const [startTouched, setStartTouched] = useState(false);

  const [delegatorLocation, setDelegatorLocation] = useState<
    { status: "loading" } | { status: "ready"; code: string | null } | { status: "error" }
  >({ status: "loading" });

  const [submitting, setSubmitting] = useState(false);
  const [delegateError, setDelegateError] = useState<string | undefined>(undefined);
  const [startError, setStartError] = useState<string | undefined>(undefined);
  const [endError, setEndError] = useState<string | undefined>(undefined);
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [offline, setOffline] = useState(false);

  const effectiveDelegatorId = delegatingFor?.id ?? myEmployeeId;

  // The delegator's own location governs every instant on the form (§2.1: "the person handing
  // over is the one whose office hours matter"). Neither the picker nor the caller's own identity
  // carries it, so it is fetched once per delegator selection — the same `getEmployee` lookup
  // `EmployeeDetail.tsx` already uses, not a new API surface.
  useEffect(() => {
    let active = true;
    if (!effectiveDelegatorId) {
      setDelegatorLocation({ status: "ready", code: null });
      return;
    }
    setDelegatorLocation({ status: "loading" });
    getEmployee(effectiveDelegatorId)
      .then((emp) => {
        if (active) setDelegatorLocation({ status: "ready", code: emp.location_code });
      })
      .catch(() => {
        if (active) setDelegatorLocation({ status: "error" });
      });
    return () => {
      active = false;
    };
  }, [effectiveDelegatorId]);

  // A location fetch that failed is read the same as one that resolved to `null` — the honest
  // organization-time fallback (§2.1), not a hard stop: the form stays usable either way.
  const knownDelegatorLocationCode = delegatorLocation.status === "ready" ? delegatorLocation.code : null;
  const zoneReady = zoneLookup !== null && delegatorLocation.status !== "loading";
  const resolvedZone = zoneReady
    ? resolveZone(knownDelegatorLocationCode, zoneLookup).zone
    : null;

  // Starts defaults to the next hour boundary once the zone is known — and only once, so picking
  // a different delegator after typing a start time never silently overwrites it.
  useEffect(() => {
    if (resolvedZone && !startTouched && start === null) {
      setStart(nextHourBoundary(resolvedZone));
    }
  }, [resolvedZone, startTouched, start]);

  function focusDelegate() {
    // In ORG mode the "Delegating for" combobox renders first, so the *last* combobox in the
    // form is always Delegate — the one this error belongs to. `Combobox` forwards no ref, so
    // this queries by the role and position it already renders with.
    const inputs = document
      .getElementById("delegation-form")
      ?.querySelectorAll<HTMLInputElement>('input[role="combobox"]');
    inputs?.[inputs.length - 1]?.focus();
  }

  async function submit() {
    if (submitting) return;
    setDelegateError(undefined);
    setStartError(undefined);
    setEndError(undefined);
    setFormError(undefined);

    if (!navigator.onLine) {
      setOffline(true);
      return;
    }
    if (!delegate) {
      setDelegateError("Choose who should decide for you.");
      focusDelegate();
      return;
    }
    if (!start) {
      setStartError("Pick a time this starts.");
      return;
    }
    if (!end) {
      setEndError("Pick a time this ends.");
      return;
    }
    if (new Date(end).getTime() <= new Date(start).getTime()) {
      setEndError("End must be after the start.");
      return;
    }
    if (new Date(end).getTime() <= Date.now()) {
      setEndError("Pick a time in the future.");
      return;
    }

    setSubmitting(true);
    try {
      const created = await createDelegation({
        ...(orgMode && delegatingFor ? { delegator_employee_id: delegatingFor.id } : {}),
        delegate_employee_id: delegate.id,
        starts_at: start,
        ends_at: end,
      });
      onCreated(created, !delegatingFor, delegatingFor?.label ?? null);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setFormError(err.problem?.detail ?? err.message);
          return;
        }
        if (err.status === 422) {
          const delegateMsg = fieldError(err.problem, "delegate_employee_id");
          const endMsg = fieldError(err.problem, "ends_at");
          if (delegateMsg) {
            setDelegateError(delegateMsg);
            focusDelegate();
            return;
          }
          if (endMsg) {
            setEndError(endMsg);
            return;
          }
          setFormError(err.problem?.detail ?? err.message);
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

  const zoneNoteText =
    zoneReady && zoneLookup ? delegationZoneNote(knownDelegatorLocationCode, zoneLookup) : null;

  const startsImmediately = start !== null && new Date(start).getTime() <= Date.now();
  const consequenceNote =
    resolvedZone && start
      ? delegationConsequenceNote(startsImmediately, formatInZone(start, resolvedZone, "date"))
      : null;

  return (
    <Card id="delegation-form" className="p-6 flex flex-col gap-5">
      <SectionHeader title="Set up a delegation" />

      {orgMode ? (
        <DelegatePicker
          label="Set this up for"
          value={delegatingFor}
          onChange={setDelegatingFor}
          helper="Leave this blank to set up your own delegation."
        />
      ) : null}

      <DelegatePicker
        label="Delegate"
        value={delegate}
        onChange={setDelegate}
        helper="They'll be able to decide anything waiting on you while the delegation runs."
        {...(delegateError !== undefined ? { error: delegateError } : {})}
      />

      {zoneReady && resolvedZone ? (
        <DateRangeField
          legend="When it runs"
          start={start}
          end={end}
          onStartChange={(value) => {
            setStartTouched(true);
            setStart(value);
          }}
          onEndChange={setEnd}
          zone={resolvedZone}
          zoneNote={zoneNoteText}
          {...(startError !== undefined ? { startError } : {})}
          {...(endError !== undefined ? { endError } : {})}
        />
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
          </div>
          <Skeleton className="h-4 w-48" />
        </div>
      )}

      {consequenceNote ? <p className="text-sm text-muted">{consequenceNote}</p> : null}

      {offline ? (
        <InlineAlert tone="warning" title="You're offline. Reconnect to save this." />
      ) : null}
      {formError ? <InlineAlert tone="danger" title={formError} /> : null}

      <div className="flex flex-col gap-3 sm:flex-row">
        <Button variant="primary" onClick={() => void submit()} disabled={submitting}>
          {submitting ? "Setting up…" : "Set up delegation"}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
      </div>
    </Card>
  );
}
