import { useState } from "react";

import { getFulfilmentSettings, putFulfilmentSettings } from "@/api/client";
import type { FulfilmentSettingsIn, FulfilmentSettingsOut, PaymentTerm } from "@/api/types";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { formDialogError } from "@/lib/formError";
import { formatDateTime } from "@/lib/format";
import { errorProp } from "@/movements/errorProp";
import {
  Button,
  Card,
  ErrorState,
  FormSection,
  InlineAlert,
  PageHeader,
  Select,
  Skeleton,
  Switch,
  TextField,
} from "@/ui";
import type { SelectOption } from "@/ui";
import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { useToast } from "@opsgrids/ui";

const SETTINGS_WRITE = "operations.fulfilment.settings.write";

const CHANNELS = [
  { key: "walk_in", label: "Walk-in orders" },
  { key: "phone", label: "Phone orders" },
  { key: "manual", label: "Manual orders" },
  { key: "sales", label: "Sales orders" },
] as const;

const TERM_OPTIONS: SelectOption[] = [
  { value: "cash", label: "Cash — paid before handover" },
  { value: "prepaid", label: "Prepaid — paid before handover" },
  { value: "credit", label: "Credit — hand over now, bill later" },
];

// Invoicing before handover needs order invoicing, which is not available yet; the server refuses
// both, so they are shown but cannot be picked.
const TIMING_OPTIONS: SelectOption[] = [
  { value: "on_delivery", label: "On delivery" },
  { value: "on_accept", label: "On accept (not available yet)", disabled: true },
  { value: "on_dispatch", label: "On dispatch (not available yet)", disabled: true },
];

interface Draft {
  terms: Record<string, PaymentTerm>;
  requirePickupCode: boolean;
  taxRate: string;
  autoAssign: boolean;
  autoInvoice: boolean;
  offerTimeout: string;
  offerRounds: string;
  taskSla: string;
  alertEmail: string;
  alertPhone: string;
}

type Errors = Partial<Record<keyof Draft, string>>;

function draftFrom(s: FulfilmentSettingsOut): Draft {
  const terms: Record<string, PaymentTerm> = {};
  for (const { key } of CHANNELS) terms[key] = s.payment_defaults[key] ?? "cash";
  return {
    terms,
    requirePickupCode: s.require_pickup_code,
    taxRate: s.default_tax_rate_pct,
    autoAssign: s.auto_assignment_enabled,
    autoInvoice: s.auto_invoice_orders,
    offerTimeout: String(s.offer_timeout_seconds),
    offerRounds: String(s.offer_rounds),
    taskSla: String(s.task_sla_minutes),
    alertEmail: s.alert_email ?? "",
    alertPhone: s.alert_phone ?? "",
  };
}

function wholeNumber(value: string, min: number, max: number): number | string {
  const n = Number(value.trim());
  if (!/^\d+$/.test(value.trim()) || n < min || n > max) return `Enter a whole number from ${min} to ${max}.`;
  return n;
}

/** The draft as the server's body, or the first problem on each field. */
function validate(
  draft: Draft,
  settings: FulfilmentSettingsOut,
): { ok: true; payload: FulfilmentSettingsIn } | { ok: false; errors: Errors } {
  const errors: Errors = {};
  const timeout = wholeNumber(draft.offerTimeout, 15, 3600);
  const rounds = wholeNumber(draft.offerRounds, 1, 10);
  const sla = wholeNumber(draft.taskSla, 1, 10080);
  if (typeof timeout === "string") errors.offerTimeout = timeout;
  if (typeof rounds === "string") errors.offerRounds = rounds;
  if (typeof sla === "string") errors.taskSla = sla;
  const tax = draft.taxRate.trim();
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(tax) || Number(tax) > 100) {
    errors.taxRate = "Enter a percentage from 0 to 100, with at most 2 decimal places.";
  }
  const email = draft.alertEmail.trim();
  if (email !== "" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.alertEmail = "Enter an email address, or leave it blank.";
  const phone = draft.alertPhone.trim();
  if (phone !== "" && !/^\+?[0-9][0-9 ()-]{5,19}$/.test(phone)) {
    errors.alertPhone = "Enter a phone number of 6 to 20 digits, or leave it blank.";
  }
  if (Object.keys(errors).length > 0 || typeof timeout === "string" || typeof rounds === "string" || typeof sla === "string") {
    return { ok: false, errors };
  }
  return {
    ok: true,
    payload: {
      invoice_timing: "on_delivery",
      // Keep any channel this screen does not show (a marketplace term set elsewhere).
      payment_defaults: { ...settings.payment_defaults, ...draft.terms },
      offer_timeout_seconds: timeout,
      offer_rounds: rounds,
      require_pickup_code: draft.requirePickupCode,
      default_tax_rate_pct: tax,
      auto_assignment_enabled: draft.autoAssign,
      auto_invoice_orders: draft.autoInvoice,
      task_sla_minutes: sla,
      alert_email: email === "" ? null : email,
      alert_phone: phone === "" ? null : phone,
    },
  };
}

const SERVER_FIELD: Record<keyof Draft, string> = {
  terms: "payment_defaults",
  requirePickupCode: "require_pickup_code",
  taxRate: "default_tax_rate_pct",
  autoAssign: "auto_assignment_enabled",
  autoInvoice: "auto_invoice_orders",
  offerTimeout: "offer_timeout_seconds",
  offerRounds: "offer_rounds",
  taskSla: "task_sla_minutes",
  alertEmail: "alert_email",
  alertPhone: "alert_phone",
};

interface SettingsFormProps {
  settings: FulfilmentSettingsOut;
  warehouseId: string | undefined;
  canWrite: boolean;
  onSaved: (saved: FulfilmentSettingsOut) => void;
}

function SettingsForm({ settings, warehouseId, canWrite, onSaved }: SettingsFormProps) {
  const toast = useToast();
  const [draft, setDraft] = useState(() => draftFrom(settings));
  const [errors, setErrors] = useState<Errors>({});
  const save = useMutation((input: FulfilmentSettingsIn) => putFulfilmentSettings(input, warehouseId), {
    onSuccess: (saved) => {
      toast.success("Fulfilment settings saved.");
      onSaved(saved);
    },
  });

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const fieldError = (key: keyof Draft) => errorProp(errors[key], save.fieldErrors[SERVER_FIELD[key]]);
  const failure = formDialogError(save.error, save.fieldErrors);
  const readOnly = !canWrite;

  function submit() {
    const result = validate(draft, settings);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    void save.mutate(result.payload);
  }

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="flex flex-col gap-6"
    >
      <FormSection title="Billing at handover" description="What must be true before goods leave the counter or go to a carrier.">
        <Select
          label="Invoice timing"
          options={TIMING_OPTIONS}
          value={settings.invoice_timing}
          disabled
          helper="Raising an order invoice before handover is not available yet, so saving sets this to on delivery."
          {...errorProp(save.fieldErrors.invoice_timing)}
        />
        <TextField
          label="Default tax rate (%)"
          inputMode="decimal"
          value={draft.taxRate}
          onChange={(e) => set("taxRate", e.target.value)}
          readOnly={readOnly}
          required
          {...fieldError("taxRate")}
        />
        {CHANNELS.map(({ key, label }) => (
          <Select
            key={key}
            label={`${label}: payment term`}
            options={TERM_OPTIONS}
            value={draft.terms[key] ?? "cash"}
            onChange={(e) => set("terms", { ...draft.terms, [key]: e.target.value as PaymentTerm })}
            disabled={readOnly}
            {...errorProp(save.fieldErrors[`payment_defaults.${key}`], key === "walk_in" ? save.fieldErrors.payment_defaults : undefined)}
          />
        ))}
        <Switch
          label="Require a pickup code"
          checked={draft.requirePickupCode}
          onChange={(v) => set("requirePickupCode", v)}
          disabled={readOnly}
          description={
            draft.requirePickupCode
              ? "The customer shows a 6-digit code at the counter before the order is handed over."
              : "Orders are handed over without a pickup code."
          }
          className="col-span-full"
        />
        <Switch
          label="Invoice orders automatically"
          checked={draft.autoInvoice}
          onChange={(v) => set("autoInvoice", v)}
          disabled={readOnly}
          description={
            draft.autoInvoice
              ? "When an order is ready, a tax invoice is raised in Finance for its CRM customer at the default tax rate. Walk-in orders without a CRM customer are not invoiced."
              : "Orders do not raise invoices automatically."
          }
          className="col-span-full"
        />
      </FormSection>

      <FormSection title="Task assignment" description="How pick tasks are offered to workers and when a late one raises an alert.">
        <Switch
          label="Assign tasks automatically"
          checked={draft.autoAssign}
          onChange={(v) => set("autoAssign", v)}
          disabled={readOnly}
          description={
            draft.autoAssign
              ? "New pick tasks are offered to an available worker without a dispatcher."
              : "A dispatcher offers every pick task by hand."
          }
          className="col-span-full"
        />
        <TextField
          label="Offer timeout (seconds)"
          inputMode="numeric"
          value={draft.offerTimeout}
          onChange={(e) => set("offerTimeout", e.target.value)}
          readOnly={readOnly}
          required
          helper="How long a worker has to accept an offered task: 15 to 3600."
          {...fieldError("offerTimeout")}
        />
        <TextField
          label="Offer rounds"
          inputMode="numeric"
          value={draft.offerRounds}
          onChange={(e) => set("offerRounds", e.target.value)}
          readOnly={readOnly}
          required
          helper="How many workers a task is offered to before it waits for a dispatcher: 1 to 10."
          {...fieldError("offerRounds")}
        />
        <TextField
          label="Task SLA (minutes)"
          inputMode="numeric"
          value={draft.taskSla}
          onChange={(e) => set("taskSla", e.target.value)}
          readOnly={readOnly}
          required
          helper="A pick task or shipment open longer than this raises a delay alert."
          {...fieldError("taskSla")}
        />
      </FormSection>

      <FormSection title="Alert contacts" description="Where operational alerts are sent. Leave blank to show them only in Automation.">
        <TextField
          label="Alert email"
          type="email"
          value={draft.alertEmail}
          onChange={(e) => set("alertEmail", e.target.value)}
          readOnly={readOnly}
          optional
          {...fieldError("alertEmail")}
        />
        <TextField
          label="Alert phone"
          type="tel"
          value={draft.alertPhone}
          onChange={(e) => set("alertPhone", e.target.value)}
          readOnly={readOnly}
          optional
          {...fieldError("alertPhone")}
        />
      </FormSection>

      {failure.error !== undefined ? (
        <InlineAlert tone="danger" title={failure.error} {...(failure.reference !== undefined ? { reference: failure.reference } : {})} />
      ) : null}

      {canWrite ? (
        <div className="flex justify-end">
          <Button type="submit" loading={save.pending}>
            {save.pending ? "Saving…" : "Save settings"}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-muted">
          You can see these settings but not change them. Changing them needs the permission to change the fulfilment policy.
        </p>
      )}
    </form>
  );
}

function sourceLine(s: FulfilmentSettingsOut, forWarehouse: boolean): string {
  const changed = s.updated_at !== null ? ` Last changed ${formatDateTime(s.updated_at)}${s.updated_by !== null ? ` by ${s.updated_by}` : ""}.` : "";
  if (s.source === "default") return "Never set — these are the built-in defaults.";
  if (forWarehouse && s.source === "organization") {
    return `This warehouse follows the organization's settings. Saving here creates an override for this warehouse only.${changed}`;
  }
  return s.source === "warehouse" ? `This warehouse has its own settings.${changed}` : `The organization's settings.${changed}`;
}

/**
 * `/settings/fulfilment` — the fulfilment policy (INVENTORY-02): payment terms per channel, pickup
 * code, default tax rate, task assignment and alert contacts, for the organization or one
 * warehouse. Anyone who can read orders sees it; changing it needs
 * `operations.fulfilment.settings.write` (the server checks; the form only stops inviting it).
 */
export function FulfilmentSettingsPage() {
  const canWrite = useCan(SETTINGS_WRITE, "ORG");
  const warehouses = useWarehouseIdOptions();
  const [warehouseId, setWarehouseId] = useState<string>("");
  const scope = warehouseId === "" ? undefined : warehouseId;
  const state = useAsync(() => getFulfilmentSettings(scope), [scope]);
  const [saved, setSaved] = useState<FulfilmentSettingsOut | null>(null);
  const current = saved !== null && saved.warehouse_id === (scope ?? null) ? saved : null;
  const settings = current ?? (state.status === "success" ? state.data : null);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Orders"
        title="Fulfilment settings"
        description="Set what an order needs before handover, how pick tasks are assigned, and where alerts go."
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <Select
          label="Applies to"
          options={[{ value: "", label: "Whole organization" }, ...warehouses.options]}
          value={warehouseId}
          onChange={(e) => {
            setSaved(null);
            setWarehouseId(e.target.value);
          }}
          helper="A warehouse with its own settings uses them in place of the organization's, every field at once."
        />
        {state.status === "loading" && settings === null ? (
          <div className="flex flex-col gap-3" aria-busy="true">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-12 w-full rounded-tile" />
            <Skeleton className="h-12 w-full rounded-tile" />
          </div>
        ) : null}
        {state.status === "error" && settings === null ? (
          <ErrorState title="Couldn't load the fulfilment settings" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {settings !== null ? (
          <>
            <p className="text-sm text-muted" aria-live="polite">
              {sourceLine(settings, scope !== undefined)}
            </p>
            <SettingsForm
              key={`${scope ?? "org"}:${settings.updated_at ?? "unset"}:${settings.source}`}
              settings={settings}
              warehouseId={scope}
              canWrite={canWrite}
              onSaved={setSaved}
            />
          </>
        ) : null}
      </Card>
    </div>
  );
}
