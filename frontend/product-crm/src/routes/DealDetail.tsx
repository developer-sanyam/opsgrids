import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { deleteDeal, getDeal, listActivities, moveDealStage, revealContactPhone } from "@/api/client";
import type { Contact, DealStage } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import {
  companyById,
  contactById,
  contactsForCompany,
  ownerById,
} from "@/data/lookups";
import { isOpen, STAGES } from "@/data/stages";
import { formatDate, formatRelative } from "@/lib/format";
import { RevealField } from "@/reveal/RevealField";
import {
  Can,
  problemMessage,
  useAsync,
  useCan,
  useMutation,
} from "@opsgrids/client";
import {
  ActivityTimeline,
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  ContactStatusPill,
  EmptyState,
  ErrorState,
  Select,
  SectionHeader,
  Skeleton,
  StagePill,
} from "@/ui";
import { formatMoney, ActivityIcon, ArrowLeftIcon, BuildingIcon, MailIcon, Menu, useToast } from "@opsgrids/ui";
import { DealFormDialog } from "@/forms/DealFormDialog";
import { ActivityFormDialog } from "@/forms/ActivityFormDialog";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-sm font-semibold text-ink text-right">{children}</span>
    </div>
  );
}

function ContactRow({ contact, primary }: { contact: Contact; primary?: boolean }) {
  return (
    <div className="flex items-center gap-3 rounded-tile bg-ground/60 px-3 py-2.5">
      <Avatar name={contact.full_name} size="sm" />
      <div className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate text-sm font-semibold text-ink">
          {contact.full_name}
          {primary ? (
            <span className="ml-1.5 rounded-full bg-accent-soft px-1.5 py-0.5 text-xs font-bold text-accent-ink align-middle">
              Primary
            </span>
          ) : null}
        </span>
        <span className="truncate text-xs text-muted">{contact.title}</span>
      </div>
      <ContactStatusPill status={contact.status} />
    </div>
  );
}

function DealDetailSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-5 w-24" />
      <Card className="p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-3 h-10 w-40 rounded-tile" />
      </Card>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="p-6 lg:col-span-2">
          <Skeleton className="h-40 w-full rounded-tile" />
        </Card>
        <Card className="p-6">
          <Skeleton className="h-40 w-full rounded-tile" />
        </Card>
      </div>
    </div>
  );
}

export function DealDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const deal = useAsync(useCallback(() => getDeal(id), [id]), [id]);
  const activities = useAsync(useCallback(() => listActivities({ dealId: id }), [id]), [id]);
  const directory = useDirectory();
  const { owners, companies, contacts } = directory;
  const canLog = useCan("crm.pipeline.activity.write", "OWN");
  const hasRevealPermission = useCan("crm.book.contact.read_sensitive", "OWN");
  // The permission is presentation-only (the server refuses regardless): an absent button, not a
  // disabled one. No second factor is involved (ADR-0023 addendum, 2026-10-15).
  const canReveal = hasRevealPermission;
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [logging, setLogging] = useState(false);

  const move = useMutation(
    (stage: DealStage) => moveDealStage(id, { stage }),
    {
      onSuccess: (updated) => {
        toast.success(`Moved to ${STAGES.find((s) => s.stage === updated.stage)?.label ?? updated.stage}.`);
        deal.reload();
      },
      onError: (error) => toast.error(problemMessage(error)),
    },
  );

  const remove = useMutation(() => deleteDeal(id), {
    onSuccess: () => {
      toast.success("Deal deleted.");
      navigate("/deals");
    },
  });

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/deals"
        className="relative hit-44 inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to deals
      </Link>

      {deal.status === "loading" ? <DealDetailSkeleton /> : null}
      {deal.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load this deal"
            message={problemMessage(deal.error)}
            onRetry={deal.reload}
          />
        </Card>
      ) : null}

      {deal.status === "success"
        ? (() => {
            const d = deal.data;
            const company = companyById(companies, d.company_id);
            const owner = ownerById(owners, d.owner_id);
            const primary = contactById(contacts, d.primary_contact_id);
            const companyContacts = company
              ? contactsForCompany(contacts, company.id).filter((c) => c.id !== primary?.id)
              : [];
            const activityItems = activities.status === "success" ? activities.data.items : [];

            return (
              <>
                {/* Header */}
                <Card className="flex flex-col gap-4 p-6">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex flex-col gap-2">
                      <div className="flex items-center gap-2.5">
                        <StagePill stage={d.stage} />
                        <span className="text-xs font-medium text-faint tnum">
                          Updated {formatRelative(d.last_activity_at)}
                        </span>
                      </div>
                      <h1 className="display text-3xl font-extrabold leading-tight">{d.name}</h1>
                      {company ? (
                        <Link
                          to={`/companies/${company.id}`}
                          className="relative hit-44 inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
                        >
                          <BuildingIcon className="size-4" />
                          {company.name}
                        </Link>
                      ) : null}
                    </div>
                    <div className="flex flex-col items-start gap-3 sm:items-end">
                      <div className="flex flex-col items-start sm:items-end">
                        <span className="display text-[2rem] font-extrabold leading-none tnum">
                          {formatMoney(d.value_minor, d.currency)}
                        </span>
                        <span className="text-sm text-muted">{d.probability}% to close</span>
                      </div>
                      <Can permission="crm.pipeline.deal.write" scope="OWN">
                        <div className="flex items-center gap-2 whitespace-nowrap">
                          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
                            Edit
                          </Button>
                          <Menu
                            label={`More actions for ${d.name}`}
                            trigger="icon"
                            align="end"
                            items={[{ label: "Delete", tone: "danger", onSelect: () => setDeleting(true) }]}
                          />
                        </div>
                      </Can>
                    </div>
                  </div>

                  <Can permission="crm.pipeline.deal.write" scope="OWN">
                    <Select
                      label="Move stage"
                      value={d.stage}
                      disabled={!isOpen(d.stage) || move.pending}
                      onChange={(e) => void move.mutate(e.target.value as DealStage)}
                      options={STAGES.map((s) => ({ value: s.stage, label: s.label }))}
                      helper={
                        !isOpen(d.stage)
                          ? "Closed deals can't be moved — log a new deal instead of reopening one."
                          : "The keyboard- and touch-friendly way to move this deal, same as dragging its card on the board."
                      }
                      containerClassName="max-w-xs"
                    />
                  </Can>
                </Card>

                <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 items-start">
                  {/* Activity timeline */}
                  <Card className="flex flex-col gap-5 p-6 lg:col-span-2">
                    <SectionHeader
                      title="Activity"
                      subtitle="Every logged touch on this deal"
                      action={
                        <Can permission="crm.pipeline.activity.write" scope="OWN">
                          <Button size="sm" variant="outline" onClick={() => setLogging(true)}>
                            Log activity
                          </Button>
                        </Can>
                      }
                    />
                    {activities.status === "loading" ? (
                      <div className="flex flex-col gap-4">
                        {Array.from({ length: 4 }).map((_, i) => (
                          <Skeleton key={i} className="h-14 w-full rounded-tile" />
                        ))}
                      </div>
                    ) : null}
                    {activities.status === "error" ? (
                      <ErrorState
                        message={problemMessage(activities.error)}
                        onRetry={activities.reload}
                      />
                    ) : null}
                    {activities.status === "success" ? (
                      activityItems.length > 0 ? (
                        <ActivityTimeline activities={activityItems} showCompany={false} />
                      ) : (
                        <EmptyState
                          icon={<ActivityIcon className="size-6" />}
                          title="No activity yet"
                          description="Log a call, email or meeting to start the timeline for this deal."
                          action={
                            canLog ? (
                              <Button size="sm" onClick={() => setLogging(true)}>
                                Log activity
                              </Button>
                            ) : null
                          }
                        />
                      )
                    ) : null}
                  </Card>

                  {/* Side: facts + contacts */}
                  <div className="flex flex-col gap-6">
                    <Card className="flex flex-col gap-1 p-6">
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
                        <Fact label="Close date">
                          <span className="tnum">{formatDate(d.close_date)}</span>
                        </Fact>
                        <Fact label="Probability">
                          <span className="tnum">{d.probability}%</span>
                        </Fact>
                        <Fact label="Source">{d.source || "—"}</Fact>
                        <Fact label="Created">
                          <span className="tnum">{formatDate(d.created_at)}</span>
                        </Fact>
                      </div>
                    </Card>

                    <Card className="flex flex-col gap-4 p-6">
                      <SectionHeader title="Contacts" subtitle={company?.name ?? ""} />
                      <div className="flex flex-col gap-2">
                        {primary ? <ContactRow contact={primary} primary /> : null}
                        {companyContacts.map((c) => (
                          <ContactRow key={c.id} contact={c} />
                        ))}
                        {!primary && companyContacts.length === 0 ? (
                          <p className="py-4 text-center text-sm text-muted">
                            No contacts linked to this deal yet.
                          </p>
                        ) : null}
                      </div>
                      {primary ? (
                        <div className="flex flex-col gap-1.5 border-t border-line pt-4 text-sm">
                          <a
                            href={`mailto:${primary.email}`}
                            className="relative hit-44 inline-flex items-center gap-2 text-ink-soft hover:text-accent transition-colors"
                          >
                            <MailIcon className="size-4 text-faint" />
                            {primary.email}
                          </a>
                          <RevealField
                            kind="phone"
                            masked={primary.phone}
                            subject="contact"
                            canReveal={canReveal}
                            resetKey={primary.id}
                            load={() => revealContactPhone(primary.id)}
                          />
                        </div>
                      ) : null}
                    </Card>
                  </div>
                </div>

                {editing ? (
                  <DealFormDialog
                    deal={d}
                    companies={companies}
                    contacts={contacts}
                    owners={owners}
                    onOwnerCreated={() => directory.reload()}
                    onClose={() => setEditing(false)}
                    onSaved={() => {
                      setEditing(false);
                      toast.success(`${d.name} saved.`);
                      deal.reload();
                    }}
                  />
                ) : null}

                {logging ? (
                  <ActivityFormDialog
                    context={{ dealId: d.id, label: d.name }}
                    onClose={() => setLogging(false)}
                    onSaved={() => {
                      setLogging(false);
                      toast.success("Activity logged.");
                      activities.reload();
                      deal.reload();
                    }}
                  />
                ) : null}

                <ConfirmDialog
                  open={deleting}
                  onClose={() => {
                    setDeleting(false);
                    remove.reset();
                  }}
                  onConfirm={() => void remove.mutate(undefined)}
                  title="Delete this deal?"
                  body={`${d.name} is removed from the pipeline. This can't be undone.`}
                  confirmLabel="Delete"
                  pendingLabel="Deleting…"
                  pending={remove.pending}
                  {...(remove.error ? { error: problemMessage(remove.error) } : {})}
                />
              </>
            );
          })()
        : null}

    </div>
  );
}
