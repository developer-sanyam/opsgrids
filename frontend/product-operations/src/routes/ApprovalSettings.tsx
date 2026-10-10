import { useState } from "react";

import { getControlSettings, putControlSettings } from "@/api/movements";
import type { ControlSettingsIn, ControlSettingsOut } from "@/api/movementTypes";
import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { validateThresholds } from "@/lib/adjustmentForm";
import type { ThresholdErrors } from "@/lib/adjustmentForm";
import { formDialogError } from "@/lib/formError";
import { formatDateTime } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { errorProp } from "@/movements/errorProp";
import { Button, Card, ErrorState, InlineAlert, PageHeader, SectionHeader, Skeleton, TextField } from "@/ui";
import { useToast } from "@opsgrids/ui";

interface ThresholdFormProps {
  settings: ControlSettingsOut;
  canConfigure: boolean;
  onSaved: (saved: ControlSettingsOut) => void;
}

function ThresholdForm({ settings, canConfigure, onSaved }: ThresholdFormProps) {
  const toast = useToast();
  const [transfer, setTransfer] = useState(settings.transfer_approval_threshold ?? "");
  const [adjustment, setAdjustment] = useState(settings.adjustment_approval_threshold ?? "");
  const [errors, setErrors] = useState<ThresholdErrors>({});

  const save = useMutation((input: ControlSettingsIn) => putControlSettings(input), {
    onSuccess: (saved) => {
      toast.success("Approval thresholds saved.");
      onSaved(saved);
    },
  });

  function submit() {
    const result = validateThresholds({ transfer, adjustment });
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    void save.mutate(result.payload);
  }

  const failure = formDialogError(save.error, save.fieldErrors);
  const unchanged =
    transfer.trim() === (settings.transfer_approval_threshold ?? "") &&
    adjustment.trim() === (settings.adjustment_approval_threshold ?? "");

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="flex flex-col gap-4"
    >
      <TextField
        label="Transfer approval threshold"
        inputMode="decimal"
        value={transfer}
        onChange={(e) => setTransfer(e.target.value)}
        readOnly={!canConfigure}
        optional
        helper="Compared with each line's quantity, in that item's own unit. A transfer with any line at or over this goes to the approval service. Leave blank for no threshold — every transfer then needs a direct sign-off from someone other than the requester."
        {...errorProp(errors.transfer, save.fieldErrors.transfer_approval_threshold)}
      />
      <TextField
        label="Adjustment approval threshold"
        inputMode="decimal"
        value={adjustment}
        onChange={(e) => setAdjustment(e.target.value)}
        readOnly={!canConfigure}
        optional
        helper="An adjustment whose change, in the item's own unit, is at or over this is held for approval and needs an evidence document. Leave blank for no threshold — adjustments then apply at once."
        {...errorProp(errors.adjustment, save.fieldErrors.adjustment_approval_threshold)}
      />

      {failure.error !== undefined ? (
        <InlineAlert
          tone="danger"
          title={failure.error}
          {...(failure.reference !== undefined ? { reference: failure.reference } : {})}
        />
      ) : null}

      {canConfigure ? (
        <div className="flex justify-end">
          <Button type="submit" loading={save.pending} disabled={unchanged}>
            {save.pending ? "Saving…" : "Save thresholds"}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-muted">
          You can see these thresholds but not change them. Changing them needs the permission to configure
          inventory controls.
        </p>
      )}
    </form>
  );
}

function Current({ settings }: { settings: ControlSettingsOut }) {
  return (
    <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
      <div className="flex flex-col gap-1">
        <dt className="text-muted">Transfer threshold</dt>
        <dd className="tnum font-semibold text-ink">
          {settings.transfer_approval_threshold === null
            ? "None — every transfer needs a direct sign-off"
            : `${formatQty(settings.transfer_approval_threshold)} on any line`}
        </dd>
      </div>
      <div className="flex flex-col gap-1">
        <dt className="text-muted">Adjustment threshold</dt>
        <dd className="tnum font-semibold text-ink">
          {settings.adjustment_approval_threshold === null
            ? "None — adjustments apply at once"
            : `${formatQty(settings.adjustment_approval_threshold)} in one change`}
        </dd>
      </div>
    </dl>
  );
}

/**
 * `/inventory/settings/approvals` — the two quantity thresholds above which transfers and
 * adjustments need approval. Anyone who can read stock sees them; changing them needs
 * `operations.inventory.control.configure` (the server checks, the form just doesn't invite it).
 */
export function ApprovalSettingsPage() {
  const canConfigure = useCan("operations.inventory.control.configure", "ORG");
  const state = useAsync(() => getControlSettings(), []);
  const [saved, setSaved] = useState<ControlSettingsOut | null>(null);
  const settings = saved ?? (state.status === "success" ? state.data : null);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Approval thresholds"
        description="Set how large a transfer or an adjustment can be before it waits for approval."
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        {state.status === "loading" && settings === null ? (
          <div className="flex flex-col gap-3" aria-busy="true">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-12 w-full rounded-tile" />
            <Skeleton className="h-12 w-full rounded-tile" />
          </div>
        ) : null}
        {state.status === "error" && settings === null ? (
          <ErrorState title="Couldn't load the approval thresholds" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {settings !== null ? (
          <>
            <SectionHeader
              title="Current thresholds"
              subtitle={
                settings.updated_at !== null
                  ? `Last changed ${formatDateTime(settings.updated_at)}${settings.updated_by !== null ? ` by ${settings.updated_by}` : ""}.`
                  : "Never set — nothing waits for approval by threshold yet."
              }
            />
            <Current settings={settings} />
          </>
        ) : null}
      </Card>

      {settings !== null ? (
        <Card className="p-4 sm:p-6 flex flex-col gap-4">
          <SectionHeader title={canConfigure ? "Change thresholds" : "Thresholds"} />
          <ThresholdForm
            key={settings.updated_at ?? "unset"}
            settings={settings}
            canConfigure={canConfigure}
            onSaved={setSaved}
          />
        </Card>
      ) : null}
    </div>
  );
}
