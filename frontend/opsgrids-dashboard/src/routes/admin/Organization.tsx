import { useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { Button, Card, ErrorState, InlineAlert, Select, Skeleton, TextField, useToast } from "@opsgrids/ui";

import { getOrganization, updateOrganization } from "@/api";
import type { Organization, OrganizationPatch } from "@/api/types";
import { COUNTRIES, LOCALES, MONTHS, TIMEZONES } from "@/lib/geo";
import { formatDate } from "@/lib/money";
import { PageHeader } from "@/ui";

const WRITE_PERMISSION = "core.org.organization.write";

/** Read/patch `GET`/`PATCH /organization`. Fields that cannot be null-ed (server drops them) are
 * simply not offered a way to clear. */
export function OrganizationPage() {
  const toast = useToast();
  const org = useAsync(getOrganization, []);
  const canWrite = useCan(WRITE_PERMISSION, "ORG");

  if (org.status === "loading") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Organization" description="Your organisation's profile, as every product in OpsGrids reads it." />
        <Skeleton className="h-96 w-full rounded-card" />
      </div>
    );
  }

  if (org.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Administration" title="Organization" description="Your organisation's profile, as every product in OpsGrids reads it." />
        <ErrorState title="Couldn't load your organisation" message={problemMessage(org.error)} onRetry={org.reload} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Administration" title="Organization" description="Your organisation's profile, as every product in OpsGrids reads it." />
      {!canWrite ? (
        <InlineAlert tone="info" title="You can see this page but not change it.">
          Ask your organisation's owner for the organisation-write permission.
        </InlineAlert>
      ) : null}
      <OrganizationForm
        organization={org.data}
        canWrite={canWrite}
        onSaved={() => {
          toast.success("Organisation saved.");
          org.reload();
        }}
      />
    </div>
  );
}

function OrganizationForm({
  organization,
  canWrite,
  onSaved,
}: {
  organization: Organization;
  canWrite: boolean;
  onSaved: (org: Organization) => void;
}) {
  const [name, setName] = useState(organization.name);
  const [legalName, setLegalName] = useState(organization.legal_name ?? "");
  const [industry, setIndustry] = useState(organization.industry ?? "");
  const [country, setCountry] = useState(organization.country ?? "");
  const [stateCode, setStateCode] = useState(organization.state_code ?? "");
  const [timezone, setTimezone] = useState(organization.timezone);
  const [locale, setLocale] = useState(organization.locale);
  const [fiscalMonth, setFiscalMonth] = useState(String(organization.fiscal_year_start_month));
  const [contactEmail, setContactEmail] = useState(organization.primary_contact_email ?? "");
  const [addressLine1, setAddressLine1] = useState(String(organization.registered_address?.line1 ?? ""));
  const [addressCity, setAddressCity] = useState(String(organization.registered_address?.city ?? ""));
  const [addressState, setAddressState] = useState(String(organization.registered_address?.state ?? ""));
  const [addressPostal, setAddressPostal] = useState(String(organization.registered_address?.postal_code ?? ""));
  const [nameError, setNameError] = useState<string | undefined>(undefined);

  const save = useMutation((patch: OrganizationPatch) => updateOrganization(patch), {
    onSuccess: (result) => onSaved(result),
  });

  function submit() {
    setNameError(undefined);
    if (name.trim().length < 1) {
      setNameError("Give the organisation a name.");
      return;
    }
    const patch: OrganizationPatch = {
      name: name.trim(),
      timezone,
      locale,
      fiscal_year_start_month: Number(fiscalMonth),
    };
    if (legalName.trim()) patch.legal_name = legalName.trim();
    if (industry.trim()) patch.industry = industry.trim();
    if (country) patch.country = country;
    if (stateCode.trim()) patch.state_code = stateCode.trim();
    if (contactEmail.trim()) patch.primary_contact_email = contactEmail.trim();
    if (addressLine1.trim() || addressCity.trim() || addressState.trim() || addressPostal.trim()) {
      patch.registered_address = {
        line1: addressLine1.trim(),
        city: addressCity.trim(),
        state: addressState.trim(),
        postal_code: addressPostal.trim(),
      };
    }
    void save.mutate(patch);
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      noValidate
      className="flex flex-col gap-6"
    >
      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-lg font-bold tracking-tight">Identity</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label="Organisation name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!canWrite}
            error={nameError ?? save.fieldErrors.name}
          />
          <TextField
            label="Legal name"
            optional
            value={legalName}
            onChange={(e) => setLegalName(e.target.value)}
            disabled={!canWrite}
            error={save.fieldErrors.legal_name}
          />
          <TextField label="Handle" value={organization.slug} disabled helper="Cannot be changed." />
          <TextField label="Currency" value={organization.currency} disabled helper="Cannot be changed." />
          <TextField label="Status" value={organization.status} disabled />
          <TextField
            label="Trial ends"
            value={organization.trial_ends_at ? formatDate(organization.trial_ends_at) : "—"}
            disabled
          />
          <TextField
            label="Industry"
            optional
            value={industry}
            onChange={(e) => setIndustry(e.target.value)}
            disabled={!canWrite}
            error={save.fieldErrors.industry}
          />
          <TextField
            label="Primary contact email"
            type="email"
            optional
            value={contactEmail}
            onChange={(e) => setContactEmail(e.target.value)}
            disabled={!canWrite}
            error={save.fieldErrors.primary_contact_email}
          />
        </div>
      </Card>

      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-lg font-bold tracking-tight">Locale</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Select
            label="Country"
            placeholder="Choose a country"
            options={COUNTRIES}
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            disabled={!canWrite}
          />
          <TextField
            label="State / region code"
            optional
            value={stateCode}
            onChange={(e) => setStateCode(e.target.value)}
            disabled={!canWrite}
            error={save.fieldErrors.state_code}
          />
          <Select
            label="Timezone"
            options={TIMEZONES}
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            disabled={!canWrite}
          />
          <Select
            label="Locale"
            options={LOCALES}
            value={locale}
            onChange={(e) => setLocale(e.target.value)}
            disabled={!canWrite}
          />
          <Select
            label="Fiscal year starts"
            options={MONTHS}
            value={fiscalMonth}
            onChange={(e) => setFiscalMonth(e.target.value)}
            disabled={!canWrite}
          />
        </div>
      </Card>

      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-lg font-bold tracking-tight">Registered address</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField label="Address line" optional value={addressLine1} onChange={(e) => setAddressLine1(e.target.value)} disabled={!canWrite} />
          <TextField label="City" optional value={addressCity} onChange={(e) => setAddressCity(e.target.value)} disabled={!canWrite} />
          <TextField label="State" optional value={addressState} onChange={(e) => setAddressState(e.target.value)} disabled={!canWrite} />
          <TextField label="Postal code" optional value={addressPostal} onChange={(e) => setAddressPostal(e.target.value)} disabled={!canWrite} />
        </div>
      </Card>

      {save.error && Object.keys(save.fieldErrors).length === 0 ? (
        <InlineAlert tone="danger" title={problemMessage(save.error)} />
      ) : null}

      {canWrite ? (
        <div className="flex justify-end">
          <Button type="submit" loading={save.pending}>
            {save.pending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      ) : null}
    </form>
  );
}
