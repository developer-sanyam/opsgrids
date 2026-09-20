import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";

import { getDeal, listActivities } from "@/api/client";
import type { Contact } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import {
  companyById,
  contactById,
  contactsForCompany,
  ownerById,
} from "@/data/lookups";
import { formatDate, formatMoney, formatRelative } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  ActivityTimeline,
  Avatar,
  Card,
  ContactStatusPill,
  EmptyState,
  ErrorState,
  SectionHeader,
  Skeleton,
  StagePill,
} from "@/ui";
import { ActivityIcon, ArrowLeftIcon, BuildingIcon, MailIcon, PhoneIcon } from "@opsgrids/ui";

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
            <span className="ml-1.5 rounded-full bg-accent-soft px-1.5 py-0.5 text-[0.65rem] font-bold text-accent-ink align-middle">
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
  const deal = useAsync(useCallback(() => getDeal(id), [id]), [id]);
  const activities = useAsync(useCallback(() => listActivities({ dealId: id }), [id]), [id]);
  const { owners, companies, contacts } = useDirectory();

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/deals"
        className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to deals
      </Link>

      {deal.status === "loading" ? <DealDetailSkeleton /> : null}
      {deal.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={deal.error.message} />
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
                          className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
                        >
                          <BuildingIcon className="size-4" />
                          {company.name}
                        </Link>
                      ) : null}
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="display text-[2rem] font-extrabold leading-none tnum">
                        {formatMoney(d.value_minor, d.currency)}
                      </span>
                      <span className="text-sm text-muted">{d.probability}% to close</span>
                    </div>
                  </div>
                </Card>

                <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 items-start">
                  {/* Activity timeline */}
                  <Card className="flex flex-col gap-5 p-6 lg:col-span-2">
                    <SectionHeader title="Activity" subtitle="Every logged touch on this deal" />
                    {activities.status === "loading" ? (
                      <div className="flex flex-col gap-4">
                        {Array.from({ length: 4 }).map((_, i) => (
                          <Skeleton key={i} className="h-14 w-full rounded-tile" />
                        ))}
                      </div>
                    ) : null}
                    {activities.status === "error" ? (
                      <ErrorState message={activities.error.message} />
                    ) : null}
                    {activities.status === "success" ? (
                      activities.data.length > 0 ? (
                        <ActivityTimeline activities={activities.data} showCompany={false} />
                      ) : (
                        <EmptyState
                          icon={<ActivityIcon className="size-6" />}
                          title="No activity yet"
                          description="Log a call, email or meeting to start the timeline for this deal."
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
                        <Fact label="Source">{d.source}</Fact>
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
                            className="inline-flex items-center gap-2 text-ink-soft hover:text-accent transition-colors"
                          >
                            <MailIcon className="size-4 text-faint" />
                            {primary.email}
                          </a>
                          <span className="inline-flex items-center gap-2 text-ink-soft">
                            <PhoneIcon className="size-4 text-faint" />
                            <span className="tnum">{primary.phone}</span>
                          </span>
                        </div>
                      ) : null}
                    </Card>
                  </div>
                </div>
              </>
            );
          })()
        : null}
    </div>
  );
}
