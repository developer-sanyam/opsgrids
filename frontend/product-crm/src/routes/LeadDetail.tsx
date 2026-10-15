import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getLead, revealLeadField, updateLead } from "@/api/client";
import type { LeadStatus } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
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
    <div className="flex items-center justify-between gap-3 py-2.5">
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

  function handleConverted() {
    setConfirmingConvert(false);
    toast.success("Lead converted — a company and deal now exist for it.");
    lead.reload();
  }

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/leads"
        className="relative hit-44 inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
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

            return (
              <>
                <Card className="flex flex-col gap-4 p-6">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex flex-col gap-2">
                      <LeadStatusPill status={l.status} />
                      <h1 className="display text-3xl font-extrabold leading-tight">{l.full_name}</h1>
                      {l.company ? (
                        <span className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted">
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
                            className="w-full sm:w-auto"
                            leadingIcon={<ArrowRightIcon className="size-4" />}
                            disabled={l.status !== "qualified"}
                            aria-describedby={l.status !== "qualified" ? "convert-reason" : undefined}
                            onClick={() => setConfirmingConvert(true)}
                          >
                            Convert
                          </Button>
                          {l.status !== "qualified" ? (
                            <p id="convert-reason" className="text-xs text-muted">
                              Qualify first: only a qualified lead can be converted.
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                    </Can>
                  </div>

                  {converted ? (
                    <div className="rounded-tile bg-present-soft px-4 py-3 text-sm text-present">
                      Converted {formatDate(l.converted_at)} — a company
                      {l.converted_contact_id ? " and contact" : ""} and a deal now exist for this
                      lead.
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
                            ? "Ready to convert."
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
                    <div className="flex flex-col gap-1.5 text-sm">
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
