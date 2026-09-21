import { useState } from "react";

import { getGstRegistration } from "@/api/client";
import { gstStateName } from "@/data/gstStates";
import { ApiError, Can, problemMessage, useAsync, useCan } from "@opsgrids/client";
import { Button, Card, ErrorState, PageHeader, SectionHeader, Skeleton } from "@/ui";
import { ShieldCheckIcon, useToast } from "@opsgrids/ui";
import { GstRegistrationFormDialog } from "@/forms/GstRegistrationFormDialog";

/** One labelled fact on the registration card. */
function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-semibold uppercase tracking-wide text-faint">{label}</dt>
      <dd className={mono ? "money text-sm text-ink-soft" : "text-sm font-medium text-ink-soft"}>
        {value}
      </dd>
    </div>
  );
}

const TYPE_LABEL: Record<string, string> = {
  regular: "Regular",
  composition: "Composition",
  casual: "Casual",
};

export function SettingsPage() {
  const toast = useToast();
  const registration = useAsync(getGstRegistration, []);
  const canWrite = useCan("finance.compliance.registration.write", "ORG");
  const [editing, setEditing] = useState(false);

  const notConfigured = registration.status === "error" && registration.error instanceof ApiError && registration.error.status === 404;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Workspace" title="Settings" />

      <Card className="flex flex-col gap-5 p-6">
        <SectionHeader
          title="Tax registration"
          subtitle="The GSTIN every future invoice is raised under, and what your GST returns file against"
          action={
            registration.status === "success" ? (
              <Can permission="finance.compliance.registration.write" scope="ORG">
                <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                  Edit
                </Button>
              </Can>
            ) : undefined
          }
        />

        {registration.status === "loading" ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-tile" />
            ))}
          </div>
        ) : null}

        {notConfigured ? (
          <div className="flex flex-col items-start gap-3 rounded-tile bg-ground px-5 py-6">
            <ShieldCheckIcon className="size-6 text-muted" />
            <div className="flex flex-col gap-1">
              <p className="text-sm font-bold text-ink">No GSTIN configured</p>
              <p className="max-w-prose text-sm text-muted">
                Without a registration, invoices carry no seller GSTIN, GST returns can't be
                generated and purchases are refused. Set one up to start issuing invoices.
              </p>
            </div>
            <Can permission="finance.compliance.registration.write" scope="ORG">
              <Button size="sm" onClick={() => setEditing(true)}>
                Set up GST registration
              </Button>
            </Can>
            {!canWrite ? (
              <p className="text-xs text-muted">
                You can see this once it's set up. Ask an administrator to configure it.
              </p>
            ) : null}
          </div>
        ) : null}

        {registration.status === "error" && !notConfigured ? (
          <ErrorState
            title="Couldn't load the tax registration"
            message={problemMessage(registration.error)}
            onRetry={registration.reload}
          />
        ) : null}

        {registration.status === "success" ? (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
            <Fact label="GSTIN" value={registration.data.gstin} mono />
            <Fact
              label="State"
              value={`${registration.data.state_code} — ${registration.data.state_name || gstStateName(registration.data.state_code)}`}
            />
            <Fact label="Legal name" value={registration.data.legal_name} />
            <Fact label="Trade name" value={registration.data.trade_name || "—"} />
            <Fact label="Type" value={TYPE_LABEL[registration.data.registration_type] ?? registration.data.registration_type} />
            <Fact label="Primary" value={registration.data.is_primary ? "Yes" : "No"} />
          </dl>
        ) : null}

        {!canWrite && registration.status === "success" ? (
          <p className="text-xs text-muted">Read-only — you don't hold the permission to change it.</p>
        ) : null}
      </Card>

      {editing ? (
        <GstRegistrationFormDialog
          registration={registration.status === "success" ? registration.data : null}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            toast.success("GST registration saved.");
            registration.reload();
          }}
        />
      ) : null}
    </div>
  );
}
