import { useState } from "react";

import { getSupplierSettings } from "@/api/client";
import type { SupplierSettings } from "@/api/types";
import { gstStateName } from "@/data/gstStates";
import { SUPPLIER_FIELD_LABEL } from "@/invoice/vocabulary";
import { Can, problemMessage, useAsync, useCan } from "@opsgrids/client";
import { Button, Card, ErrorState, InlineAlert, PageHeader, SectionHeader, Skeleton } from "@/ui";
import { ShieldCheckIcon, useToast } from "@opsgrids/ui";
import { GstRegistrationFormDialog } from "@/forms/GstRegistrationFormDialog";
import { SupplierProfileFormDialog } from "@/forms/SupplierProfileFormDialog";

/** One labelled fact on a settings card. */
function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-caption font-semibold uppercase tracking-wide text-faint">{label}</dt>
      <dd className={mono ? "money break-all text-sm text-ink-soft" : "text-sm font-medium text-ink-soft"}>
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

function addressOf(settings: SupplierSettings): string {
  const r = settings.registration;
  if (!r) return "";
  const cityLine = [r.city, r.pincode].filter((s) => s && s.trim() !== "").join(" ");
  return [r.address_line1, r.address_line2, cityLine].map((s) => (s ?? "").trim()).filter(Boolean).join(", ");
}

export function SettingsPage() {
  const toast = useToast();
  const supplier = useAsync(getSupplierSettings, []);
  const canWrite = useCan("finance.compliance.registration.write", "ORG");
  const [editing, setEditing] = useState<"registration" | "profile" | null>(null);

  const data = supplier.status === "success" ? supplier.data : null;
  const registration = data?.registration ?? null;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Workspace" title="Settings" />

      {data && !data.ready ? (
        <InlineAlert tone="warning" title="Invoices can't be issued until these are filled in">
          A tax invoice must state your legal name, registered address and GSTIN. Missing:{" "}
          {data.missing.map((field) => SUPPLIER_FIELD_LABEL[field]).join(", ")}.
          {canWrite ? "" : " Ask a finance administrator to complete them."}
        </InlineAlert>
      ) : null}

      <Card className="flex flex-col gap-5 p-6">
        <SectionHeader
          title="Tax registration"
          subtitle="The GSTIN, legal name and address every future invoice is issued under"
          action={
            registration ? (
              <Can permission="finance.compliance.registration.write" scope="ORG">
                <Button variant="outline" size="sm" onClick={() => setEditing("registration")}>
                  Edit
                </Button>
              </Can>
            ) : undefined
          }
        />

        {supplier.status === "loading" ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-tile" />
            ))}
          </div>
        ) : null}

        {supplier.status === "error" ? (
          <ErrorState title="Couldn't load your supplier details" message={problemMessage(supplier.error)} onRetry={supplier.reload} />
        ) : null}

        {data && registration === null ? (
          <div className="flex flex-col items-start gap-3 rounded-tile bg-ground px-5 py-6">
            <ShieldCheckIcon className="size-6 text-muted" />
            <div className="flex flex-col gap-1">
              <p className="text-sm font-bold text-ink">No GSTIN configured</p>
              <p className="max-w-prose text-sm text-muted">
                Without a registration no tax invoice can be issued, GST returns can't be generated
                and purchases are refused. Set one up to start issuing invoices.
              </p>
            </div>
            <Can permission="finance.compliance.registration.write" scope="ORG">
              <Button size="sm" onClick={() => setEditing("registration")}>
                Set up GST registration
              </Button>
            </Can>
            {!canWrite ? (
              <p className="text-xs text-muted">You can see this once it's set up. Ask an administrator to configure it.</p>
            ) : null}
          </div>
        ) : null}

        {registration ? (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
            <Fact label="GSTIN" value={registration.gstin} mono />
            <Fact label="State" value={`${registration.state_code} — ${registration.state_name || gstStateName(registration.state_code)}`} />
            <Fact label="Legal name" value={registration.legal_name} />
            <Fact label="Trade name" value={registration.trade_name || "Not set"} />
            <div className="col-span-2">
              <Fact label="Registered address" value={data ? addressOf(data) || "Not set" : ""} />
            </div>
            <Fact label="Type" value={TYPE_LABEL[registration.registration_type] ?? registration.registration_type} />
            <Fact label="Primary" value={registration.is_primary ? "Yes" : "No"} />
          </dl>
        ) : null}

        {!canWrite && registration ? <p className="text-xs text-muted">Read-only — you don't hold the permission to change it.</p> : null}
      </Card>

      {data ? (
        <Card className="flex flex-col gap-4 p-6">
          <SectionHeader
            title="PAN and bank details"
            subtitle="Printed on every tax invoice so customers know where to pay"
            action={
              <Can permission="finance.compliance.registration.write" scope="ORG">
                <Button variant="outline" size="sm" onClick={() => setEditing("profile")}>
                  {data.profile.pan || data.profile.bank.account_number ? "Edit" : "Add"}
                </Button>
              </Can>
            }
          />
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
            <Fact label="PAN" value={data.profile.pan ?? "Not set"} mono />
            <Fact label="Account name" value={data.profile.bank.account_name || "Not set"} />
            <Fact label="Account number" value={data.profile.bank.account_number || "Not set"} mono />
            <Fact label="IFSC" value={data.profile.bank.ifsc || "Not set"} mono />
            <Fact label="Bank" value={data.profile.bank.bank_name || "Not set"} />
            <Fact label="Branch" value={data.profile.bank.branch || "Not set"} />
            <Fact label="UPI ID" value={data.profile.bank.upi_id || "Not set"} />
          </dl>
          <p className="text-xs text-muted">
            The account number is stored encrypted and shown masked here. It appears in full only on
            a printed invoice, and every such view is recorded in the audit log.
          </p>
        </Card>
      ) : null}

      {editing === "registration" ? (
        <GstRegistrationFormDialog
          registration={registration}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            toast.success("GST registration saved.");
            supplier.reload();
          }}
        />
      ) : null}
      {editing === "profile" && data ? (
        <SupplierProfileFormDialog
          settings={data}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            toast.success("PAN and bank details saved.");
            supplier.reload();
          }}
        />
      ) : null}
    </div>
  );
}
