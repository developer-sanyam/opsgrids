import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getLead, revealLeadField, updateLead } from "@/api/client";
import type { Lead, LeadConvertResult, LeadStatus, Owner } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { useMyRep } from "@/data/useMyRep";
import { ownerById } from "@/data/lookups";
import { ConvertLeadDialog } from "@/forms/ConvertLeadDialog";
import { formatDate } from "@/lib/format";
import { RevealField } from "@/reveal/RevealField";
import {
  Can,
  problemMessage,
  useAsync,
  useCan,
  useMutation,
} from "@opsgrids/client";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  BuildingIcon,
  cn,
  InlineAlert,
  useToast,
} from "@opsgrids/ui";
import {
  Avatar,
  Button,
  Card,
  LeadStatusPill,
  SectionHeader,
  Select,
  Skeleton,
} from "@/ui";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-3">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-sm font-semibold text-ink text-right">{children}</span>
    </div>
  );
}

/** What the masked-fields footnote says: no grant, or the ordinary reminder. Never disables the
 * Reveal button — the button is absent instead (`RevealableField`'s own rule) — this only
 * explains why. */
function RevealNotice({ hasPermission }: { hasPermission: boolean }) {
  if (!hasPermission) return <>Ask an administrator if you need the real values.</>;
  return <>Revealing one is recorded.</>;
}

const STATUS_LABELS: Record<LeadStatus, string> = {
  new: "New",
  working: "Working",
  qualified: "Qualified",
  disqualified: "Disqualified",
  converted: "Converted",
};

/**
 * The status control's options: the lead's current status, then only the moves the server will
 * accept (`LeadOut.next_statuses` -- its own state machine, so this can never offer New -> Qualified
 * or the status the lead already has). `converted` is never among them: only Convert reaches it.
 */
function statusOptions(current: LeadStatus, next: readonly LeadStatus[]): { value: LeadStatus; label: string }[] {
  return [current, ...next.filter((s) => s !== current)].map((value) => ({
    value,
    label: value === current ? `${STATUS_LABELS[value]} (current)` : `Mark as ${STATUS_LABELS[value]}`,
  }));
}

/** The owner control's value for "me, put me on the roster first". Never a real id. */
const ME = "__me__";

/**
 * Who works the lead, changeable here -- `PATCH /leads/{id}` with `owner_id`, which the server
 * re-scopes (an OWN-scoped rep may only take a lead themselves) and checks is an active rep. A
 * lead captured with no owner must be assignable from its own page: Convert refuses one without.
 * Only active reps are offered; a login not on the roster yet may pick "Me", which adds them
 * (`PUT /customers/owners/me`) first. Unassigning is not offered: it needs ORG scope and leaves
 * the lead unconvertible.
 */
function OwnerControl({ lead, owners, onChanged }: { lead: Lead; owners: Owner[]; onChanged: () => void }) {
  const toast = useToast();
  const myRep = useMyRep();
  const current = ownerById(owners, lead.owner_id);
  const assign = useMutation(
    async (value: string) => {
      const ownerId = value === ME ? (await myRep.ensure())?.id : value;
      if (ownerId === undefined) return null;
      return updateLead(lead.id, { owner_id: ownerId });
    },
    {
      onSuccess: (saved) => {
        if (saved === null) return;
        toast.success("Owner updated.");
        onChanged();
      },
    },
  );

  const options = owners
    .filter((o) => o.is_active || o.id === lead.owner_id)
    .map((o) => ({ value: o.id, label: o.is_active ? o.full_name : `${o.full_name} (inactive)` }));
  if (myRep.rep === undefined) options.push({ value: ME, label: "Me" });

  return (
    <div className="flex flex-col gap-2 py-3">
      <Select
        label="Owner"
        value={current ? current.id : ""}
        disabled={assign.pending}
        placeholder="Choose who works this lead"
        options={options}
        onChange={(e) => {
          const next = e.target.value;
          if (next !== "" && next !== lead.owner_id) void assign.mutate(next);
        }}
        helper={lead.owner_id === null ? "A lead needs an owner before it can be converted." : undefined}
        containerClassName="max-w-xs"
      />
      {assign.error ? (
        <InlineAlert tone="danger" title="Couldn't change the owner">
          {problemMessage(assign.error)}
        </InlineAlert>
      ) : null}
    </div>
  );
}

function LeadDetailSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-5 w-24" />
      <Card className="p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-3 h-10 w-40 rounded-tile" />
      </Card>
      <Card className="p-6">
        <Skeleton className="h-40 w-full rounded-tile" />
      </Card>
    </div>
  );
}

export function LeadDetailPage() {
  const { id = "" } = useParams();
  const toast = useToast();
  const lead = useAsync(useCallback(() => getLead(id), [id]), [id]);
  const directory = useDirectory();
  const { owners } = directory;
  const hasRevealPermission = useCan("crm.lead.lead.read_sensitive", "OWN");
  // The permission is presentation-only (the server refuses the call regardless, per
  // `RevealableField`'s own doc comment): an absent control, not a disabled one. No second
  // factor is involved (ADR-0023 addendum, 2026-10-15).
  const canRevealDetails = hasRevealPermission;
  const [confirmingConvert, setConfirmingConvert] = useState(false);

  const setStatus = useMutation((status: LeadStatus) => updateLead(id, { status }), {
    onSuccess: () => {
      toast.success("Status updated.");
      lead.reload();
    },
  });

  function handleConverted(result: LeadConvertResult) {
    setConfirmingConvert(false);
    toast.success(
      result.contact_id
        ? "Lead converted — a company, contact and deal now exist for it."
        : "Lead converted — a company and deal now exist for it.",
    );
    lead.reload();
  }

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/leads"
        className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to leads
      </Link>

      {lead.status === "loading" ? <LeadDetailSkeleton /> : null}
      {lead.status === "error" ? (
        <Card className="p-6">
          <div className="text-sm text-negative">{problemMessage(lead.error)}</div>
          <Button size="sm" variant="outline" className="mt-3" onClick={lead.reload}>
            Try again
          </Button>
        </Card>
      ) : null}

      {lead.status === "success"
        ? (() => {
            const l = lead.data;
            const owner = ownerById(owners, l.owner_id);
            const converted = l.status === "converted";
            const hasOwner = l.owner_id !== null;
            const convertBlockers = [
              ...(l.status !== "qualified" ? ["Qualify first: only a qualified lead can be converted."] : []),
              ...(!hasOwner ? ["Assign an owner first: the company, contact and deal it creates need one."] : []),
            ];
            const convertBlocked = convertBlockers.length > 0;

            return (
              <>
                <Card className="flex flex-col gap-4 p-6">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex flex-col gap-2">
                      <LeadStatusPill status={l.status} />
                      <h1 className="display text-3xl font-extrabold leading-tight">{l.full_name}</h1>
                      {l.company ? (
                        <span className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted">
                          <BuildingIcon className="size-4" />
                          {l.company}
                        </span>
                      ) : null}
                    </div>
                    <Can permission="crm.lead.lead.convert" scope="OWN">
                      {!converted ? (
                        <div className="flex w-full flex-col gap-1 sm:w-auto sm:items-end">
                          <Button
                            size="md"
                            leadingIcon={<ArrowRightIcon className="size-4" />}
                            // aria-disabled, not disabled: a disabled button leaves the tab order
                            // and takes its reason with it.
                            aria-disabled={convertBlocked || undefined}
                            aria-describedby={convertBlocked ? "convert-reason" : undefined}
                            className={cn("w-full sm:w-auto", convertBlocked && "cursor-not-allowed opacity-50")}
                            onClick={() => {
                              if (!convertBlocked) setConfirmingConvert(true);
                            }}
                          >
                            Convert
                          </Button>
                          {convertBlocked ? (
                            <div id="convert-reason" className="flex flex-col gap-1 text-caption text-muted sm:text-right">
                              {convertBlockers.map((reason) => (
                                <p key={reason}>{reason}</p>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </Can>
                  </div>

                  {converted ? (
                    <div className="flex flex-col gap-2 rounded-tile bg-present-soft px-4 py-3 text-sm text-present">
                      <p>
                        Converted {formatDate(l.converted_at)} — a company
                        {l.converted_contact_id ? " and contact" : ""} and a deal now exist for this
                        lead.
                      </p>
                      <div className="flex flex-wrap gap-x-4">
                        {l.converted_company_id ? (
                          <Link
                            to={`/companies/${l.converted_company_id}`}
                            className="relative hit-44 inline-flex items-center gap-1 font-semibold underline underline-offset-2 hover:no-underline"
                          >
                            Open the company
                          </Link>
                        ) : null}
                        {l.converted_deal_id ? (
                          <Link
                            to={`/deals/${l.converted_deal_id}`}
                            className="relative hit-44 inline-flex items-center gap-1 font-semibold underline underline-offset-2 hover:no-underline"
                          >
                            Open the deal
                          </Link>
                        ) : null}
                      </div>
                    </div>
                  ) : (
                    <Can permission="crm.lead.lead.write" scope="OWN">
                      <Select
                        label="Status"
                        value={l.status}
                        disabled={setStatus.pending}
                        onChange={(e) => {
                          const next = e.target.value as LeadStatus;
                          if (next !== l.status) void setStatus.mutate(next);
                        }}
                        options={statusOptions(l.status, l.next_statuses)}
                        helper={
                          l.status === "qualified"
                            ? hasOwner
                              ? "Ready to convert."
                              : "Qualified. Assign an owner below, then convert."
                            : l.status === "new"
                              ? "Mark it Working once you start on it; then Qualify it to convert."
                              : l.status === "working"
                                ? "Qualify this lead before it can be converted."
                                : "Reopen it to work on it again."
                        }
                        containerClassName="max-w-xs"
                      />
                      {setStatus.error ? (
                        // Said where the rep is looking, in the server's own plain words.
                        <InlineAlert tone="danger" title="Couldn't change the status">
                          {problemMessage(setStatus.error)}
                        </InlineAlert>
                      ) : null}
                    </Can>
                  )}
                </Card>

                <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 items-start">
                  <Card className="flex flex-col gap-1 p-6 lg:col-span-2">
                    <SectionHeader title="Details" />
                    <div className="mt-1 divide-y divide-line">
                      {(() => {
                        const ownerFact = (
                          <Fact label="Owner">
                            {owner ? (
                              <span className="inline-flex items-center gap-2">
                                <Avatar name={owner.full_name} size="sm" />
                                {owner.full_name}
                              </span>
                            ) : (
                              "Unassigned"
                            )}
                          </Fact>
                        );
                        return converted ? (
                          ownerFact
                        ) : (
                          <Can permission="crm.lead.lead.write" scope="OWN" fallback={ownerFact}>
                            <OwnerControl lead={l} owners={owners} onChanged={lead.reload} />
                          </Can>
                        );
                      })()}
                      <Fact label="Source">
                        <span className="capitalize">{l.source}</span>
                      </Fact>
                      <Fact label="Captured">
                        <span className="tnum">{formatDate(l.created_at)}</span>
                      </Fact>
                    </div>
                    {l.notes ? (
                      <div className="mt-3 rounded-tile bg-ground/60 p-3 text-sm text-ink-soft">
                        {l.notes}
                      </div>
                    ) : null}
                  </Card>

                  <Card className="flex flex-col gap-3 p-6">
                    <SectionHeader title="Contact" />
                    <div className="flex flex-col gap-2 text-sm">
                      <RevealField
                        kind="email"
                        masked={l.email}
                        subject="lead"
                        canReveal={canRevealDetails}
                        resetKey={l.id}
                        load={() => revealLeadField(l.id, "email")}
                      />
                      <RevealField
                        kind="phone"
                        masked={l.phone}
                        subject="lead"
                        canReveal={canRevealDetails}
                        resetKey={l.id}
                        load={() => revealLeadField(l.id, "phone")}
                      />
                    </div>
                    {l.email !== null || l.phone !== null ? (
                      <p className="text-xs text-faint">
                        Email and phone are masked.{" "}
                        <RevealNotice hasPermission={hasRevealPermission} />
                        {converted && l.converted_contact_id
                          ? " This lead has been converted; the contact created from it is under Contacts."
                          : ""}
                      </p>
                    ) : null}
                  </Card>
                </div>

                {confirmingConvert ? (
                  <ConvertLeadDialog
                    lead={l}
                    onClose={() => setConfirmingConvert(false)}
                    onConverted={handleConverted}
                  />
                ) : null}
              </>
            );
          })()
        : null}

    </div>
  );
}
