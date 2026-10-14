import { useState } from "react";

import { ApiError } from "@opsgrids/client";
import {
  Button,
  ConfirmDialog,
  Dialog,
  InlineAlert,
  RadioGroup,
  Switch,
  Tag,
  TextArea,
  TextField,
  useUnsavedChangesWarning,
} from "@opsgrids/ui";
import type { RadioOption } from "@opsgrids/ui";

import { MODE_DESCRIPTION, MODE_LABEL, SLOTTED_UNAVAILABLE } from "./copy";
import { createZone, updateZone } from "./api";
import type { StoreOut, ZoneOut } from "./types";
import {
  EMPTY_ZONE,
  fromZone,
  toZoneInput,
  validateZone,
  type ZoneErrors,
  type ZoneFormValues,
} from "./zoneForm";

/**
 * Create or edit a zone: a name, a mode, and the behaviour that applies to every postal code in it.
 *
 * **`SLOTTED` is visible and disabled, not hidden.** ADR-0010 §3 refuses it in the writer until
 * slice 2, and §"Consequences" says the merchant will see it named and be told it is coming. A
 * merchant who cannot see the mode opens a support ticket asking for it; a greyed option with a
 * reason beside it answers them on the spot.
 *
 * The postal codes are not here. A zone is a name plus a bag of codes, and the bag has its own
 * screen because pasting two hundred codes and reading the conflicts back needs the room.
 */
export function ZoneFormDialog({
  open,
  zone,
  store,
  onClose,
  onSaved,
}: {
  open: boolean;
  /** `null` creates. */
  zone: ZoneOut | null;
  store: StoreOut;
  onClose: () => void;
  onSaved: (zone: ZoneOut, message: string) => void;
}) {
  const [values, setValues] = useState<ZoneFormValues>(() =>
    zone ? fromZone(zone) : EMPTY_ZONE,
  );
  const [errors, setErrors] = useState<ZoneErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [confirmingClose, setConfirmingClose] = useState(false);

  // Compared against the same seed `values` initialised from — the caller mounts this only while
  // it is open and unmounts it to close (the same reset every `FormDialog` relies on), so `zone`
  // never changes under it.
  const seed = zone ? fromZone(zone) : EMPTY_ZONE;
  const dirty = JSON.stringify(values) !== JSON.stringify(seed);
  useUnsavedChangesWarning(open && dirty);

  function set<K extends keyof ZoneFormValues>(key: K, value: ZoneFormValues[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    // Clearing the field's own error as it is corrected, and nothing else: an error that persists
    // after the fix reads as the form not listening.
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function submit() {
    if (submitting) return;
    const found = validateZone(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      const input = toZoneInput(values);
      const saved = zone ? await updateZone(zone.id, input) : await createZone(input);
      onSaved(
        saved,
        zone ? `${saved.name} saved.` : `${saved.name} created. Add postal codes to it next.`,
      );
    } catch (error) {
      // Everything the merchant typed stays exactly where it is. A form that empties itself on a
      // failed save costs them the work twice.
      setFailure(
        error instanceof ApiError
          ? error.message
          : "Couldn't reach the server. Nothing was saved — try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  // Cancel, Escape and the close control ask first when there's something to lose; a successful
  // save closes through `onSaved` → the caller's own `open`, which never asks.
  function requestClose() {
    if (submitting) return;
    if (dirty) {
      setConfirmingClose(true);
      return;
    }
    onClose();
  }

  const modeOptions: RadioOption[] = [
    { value: "PROMISED", label: MODE_LABEL.PROMISED, description: MODE_DESCRIPTION.PROMISED },
    {
      value: "SLOTTED",
      label: MODE_LABEL.SLOTTED,
      description: MODE_DESCRIPTION.SLOTTED,
      disabled: true,
      aside: <Tag tone="neutral">{SLOTTED_UNAVAILABLE}</Tag>,
    },
    { value: "UNSERVED", label: MODE_LABEL.UNSERVED, description: MODE_DESCRIPTION.UNSERVED },
  ];

  const promised = values.mode === "PROMISED";

  return (
    <Dialog
      open={open}
      onClose={requestClose}
      title={zone ? `Edit ${zone.name}` : "Add a zone"}
      description="A zone is a name, a delivery behaviour, and the postal codes it covers. Postal codes are added on their own screen."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={requestClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Saving…" : zone ? "Save zone" : "Create zone"}
          </Button>
        </>
      }
    >
      {/* One summary, announced once. A `role="alert"` per field would interrupt five times on a
          five-error submit; the fields carry their own messages by `aria-describedby`. */}
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That zone wasn't saved" className="mb-4">
            {failure}
          </InlineAlert>
        ) : null}
        {failure === null && Object.keys(errors).length > 0 ? (
          <InlineAlert tone="warning" title="Check the fields marked below" className="mb-4">
            Nothing has been sent yet.
          </InlineAlert>
        ) : null}
      </div>

      <form
        className="flex flex-col gap-5"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label="Zone name"
            value={values.name}
            onChange={(event) => set("name", event.target.value)}
            placeholder="Mumbai — central"
            required
            disabled={submitting}
            {...(errors.name !== undefined ? { error: errors.name } : {})}
          />
          <TextField
            label="Zone code"
            value={values.code}
            onChange={(event) => set("code", event.target.value.toUpperCase())}
            placeholder="MUM-CENTRAL"
            helper="The handle your CSV import refers to. Unique within your store."
            required
            disabled={submitting}
            {...(errors.code !== undefined ? { error: errors.code } : {})}
          />
        </div>

        <RadioGroup
          legend="What the buyer gets"
          name="zone-mode"
          value={values.mode}
          onChange={(value) => set("mode", value as ZoneFormValues["mode"])}
          options={modeOptions}
          {...(errors.mode !== undefined ? { error: errors.mode } : {})}
        />

        {promised ? (
          <RadioGroup
            legend="How the promise is expressed"
            name="promise-kind"
            value={values.promiseKind}
            onChange={(value) => set("promiseKind", value as ZoneFormValues["promiseKind"])}
            options={[
              {
                value: "DURATION",
                label: "In about N minutes",
                description: "Quick commerce. The buyer sees “in about 30 minutes”.",
              },
              {
                value: "DATE_OFFSET",
                label: "By a date",
                description: "Courier delivery. The buyer sees “by Thursday 25 September”.",
              },
            ]}
          />
        ) : null}

        {promised && values.promiseKind === "DURATION" ? (
          <TextField
            label="Minutes"
            inputMode="numeric"
            value={values.promiseMinutes}
            onChange={(event) => set("promiseMinutes", event.target.value)}
            placeholder="30"
            helper="What the buyer is told to expect. Nothing dispatches against it yet."
            disabled={submitting}
            containerClassName="sm:max-w-64"
            {...(errors.promiseMinutes !== undefined ? { error: errors.promiseMinutes } : {})}
          />
        ) : null}

        {promised && values.promiseKind === "DATE_OFFSET" ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <TextField
              label="Days after the order"
              inputMode="numeric"
              value={values.promiseOffsetDays}
              onChange={(event) => set("promiseOffsetDays", event.target.value)}
              placeholder="2"
              helper="0 means same day."
              disabled={submitting}
              {...(errors.promiseOffsetDays !== undefined
                ? { error: errors.promiseOffsetDays }
                : {})}
            />
            <TextField
              label="Cutoff time"
              type="time"
              value={values.promiseCutoffTime}
              onChange={(event) => set("promiseCutoffTime", event.target.value)}
              optional
              /* ADR-0000 "Also settled": the relevant location's timezone, never the browser's.
                 The server resolves the date; this field is a wall clock in the store's zone and
                 says which zone that is, because a cutoff without one is not a time. */
              helper={`A wall clock in ${store.timezone}. Orders after it get one more day.`}
              disabled={submitting}
              {...(errors.promiseCutoffTime !== undefined
                ? { error: errors.promiseCutoffTime }
                : {})}
            />
          </div>
        ) : null}

        {values.mode !== "UNSERVED" ? (
          <fieldset className="m-0 flex flex-col gap-4 border-0 p-0">
            <legend className="mb-1 text-xs font-semibold uppercase tracking-wide text-faint">
              Shown to buyers — not charged
            </legend>
            {/* ADR-0010 §8: stored and displayed in slice 1, enforced in slice 4. There is no
                cart, no pricing engine and nothing that can apply these to an order, and saying
                otherwise would be the one lie on this screen a merchant could not detect. */}
            <InlineAlert tone="info" live="off" title="These are display values">
              They appear on the answer a buyer is given. Nothing charges them: there is no cart or
              checkout yet, so a fee here is copy, not a price.
            </InlineAlert>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <TextField
                label={`Delivery fee (${store.currency})`}
                inputMode="decimal"
                value={values.deliveryFee}
                onChange={(event) => set("deliveryFee", event.target.value)}
                placeholder="29"
                optional
                disabled={submitting}
                {...(errors.deliveryFee !== undefined ? { error: errors.deliveryFee } : {})}
              />
              <TextField
                label={`Free above (${store.currency})`}
                inputMode="decimal"
                value={values.freeDeliveryAbove}
                onChange={(event) => set("freeDeliveryAbove", event.target.value)}
                placeholder="499"
                optional
                disabled={submitting}
                {...(errors.freeDeliveryAbove !== undefined
                  ? { error: errors.freeDeliveryAbove }
                  : {})}
              />
              <TextField
                label={`Minimum order (${store.currency})`}
                inputMode="decimal"
                value={values.minOrder}
                onChange={(event) => set("minOrder", event.target.value)}
                placeholder="199"
                optional
                disabled={submitting}
                {...(errors.minOrder !== undefined ? { error: errors.minOrder } : {})}
              />
            </div>
          </fieldset>
        ) : null}

        <TextArea
          label="Note for the buyer"
          value={values.buyerNote}
          onChange={(event) => set("buyerNote", event.target.value)}
          placeholder="Riders are on the road from 7am to 11pm."
          maxLength={200}
          counterFrom={140}
          optional
          rows={2}
          disabled={submitting}
          {...(errors.buyerNote !== undefined ? { error: errors.buyerNote } : {})}
        />

        <Switch
          label="Zone is switched on"
          checked={values.isActive}
          onChange={(checked) => set("isActive", checked)}
          disabled={submitting}
          description={
            values.isActive
              ? "Buyers in these postal codes get this zone's answer."
              : "Buyers in these postal codes are told the store doesn't deliver there, with the reason “the zone it belongs to is switched off”."
          }
        />
      </form>

      <ConfirmDialog
        open={open && confirmingClose}
        onClose={() => setConfirmingClose(false)}
        onConfirm={() => {
          setConfirmingClose(false);
          onClose();
        }}
        title="Discard unsaved changes?"
        body={`What you've entered in "${zone ? `Edit ${zone.name}` : "Add a zone"}" hasn't been saved and will be lost.`}
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
      />
    </Dialog>
  );
}
