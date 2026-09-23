import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { convertLead, getLead, updateLead } from "@/api/client";
import type { LeadStatus } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { ownerById } from "@/data/lookups";
import { formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  BuildingIcon,
  MailIcon,
  PhoneIcon,
  useToast,
} from "@opsgrids/ui";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
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

/** Plain status edits — everything except `converted`, which only `:convert` can reach. */
const STATUS_OPTIONS: { value: LeadStatus; label: string }[] = [
  { value: "new", label: "New" },
  { value: "working", label: "Working" },
  { value: "qualified", label: "Qualified" },
  { value: "disqualified", label: "Disqualified" },
];

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
  const canWrite = useCan("crm.lead.lead.write", "OWN");
  const [confirmingConvert, setConfirmingConvert] = useState(false);

  const setStatus = useMutation((status: LeadStatus) => updateLead(id, { status }), {
    onSuccess: () => {
      toast.success("Status updated.");
      lead.reload();
    },
    onError: (error) => toast.error(problemMessage(error)),
  });

  const convert = useMutation(() => convertLead(id), {
    onSuccess: () => {
      setConfirmingConvert(false);
      toast.success("Lead converted — a company and deal now exist for it.");
      lead.reload();
    },
  });

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/leads"
        className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
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
                        <Button
                          size="sm"
                          leadingIcon={<ArrowRightIcon className="size-4" />}
                          disabled={l.status !== "qualified"}
                          onClick={() => setConfirmingConvert(true)}
                        >
                          Convert
                        </Button>
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
                        onChange={(e) => void setStatus.mutate(e.target.value as LeadStatus)}
                        options={STATUS_OPTIONS}
                        helper={
                          l.status !== "qualified"
                            ? "Qualify this lead before it can be converted."
                            : "Ready to convert."
                        }
                        containerClassName="max-w-xs"
                      />
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
                      <span className="inline-flex items-center gap-2 text-ink-soft">
                        <MailIcon className="size-4 text-faint" />
                        {l.email ?? "—"}
                      </span>
                      <span className="inline-flex items-center gap-2 text-ink-soft">
                        <PhoneIcon className="size-4 text-faint" />
                        <span className="tnum">{l.phone ?? "—"}</span>
                      </span>
                    </div>
                    {!converted && canWrite ? (
                      <p className="text-xs text-faint">
                        Email and phone are masked here — the real values are sealed and never
                        shown outside a service-to-service conversion.
                      </p>
                    ) : null}
                  </Card>
                </div>

                <ConfirmDialog
                  open={confirmingConvert}
                  onClose={() => {
                    setConfirmingConvert(false);
                    convert.reset();
                  }}
                  onConfirm={() => void convert.mutate(undefined)}
                  title="Convert this lead?"
                  body={`${l.full_name} becomes a company${l.email ? " and a contact" : ""} and a deal. This can't be undone.`}
                  confirmLabel="Convert"
                  pendingLabel="Converting…"
                  pending={convert.pending}
                  {...(convert.error ? { error: problemMessage(convert.error) } : {})}
                />
              </>
            );
          })()
        : null}
    </div>
  );
}
