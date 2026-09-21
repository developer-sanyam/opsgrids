import { useCallback, useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import { getAllDeals, getCompany, listActivities } from "@/api/client";
import type { Deal } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { contactsForCompany, ownerById } from "@/data/lookups";
import { isOpen } from "@/data/stages";
import { formatDate, formatMoney, formatMoneyCompact } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  ActivityTimeline,
  Avatar,
  Card,
  CompanyStatusPill,
  ContactStatusPill,
  EmptyState,
  ErrorState,
  SectionHeader,
  Skeleton,
  StagePill,
} from "@/ui";
import {
  ActivityIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  GlobeIcon,
  HandshakeIcon,
  MapPinIcon,
} from "@opsgrids/ui";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-sm font-semibold text-ink text-right">{children}</span>
    </div>
  );
}

function DealRow({ deal }: { deal: Deal }) {
  return (
    <Link
      to={`/deals/${deal.id}`}
      className="group flex items-center gap-3 rounded-tile bg-ground/60 px-3 py-2.5 hover:bg-ground transition-colors"
    >
      <div className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate text-sm font-semibold text-ink">{deal.name}</span>
        <span className="tnum text-xs text-muted">
          {formatMoney(deal.value_minor, deal.currency)} · closes {formatDate(deal.close_date)}
        </span>
      </div>
      <StagePill stage={deal.stage} />
      <ArrowRightIcon className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-ink" />
    </Link>
  );
}

export function CompanyDetailPage() {
  const { id = "" } = useParams();
  const company = useAsync(useCallback(() => getCompany(id), [id]), [id]);
  const deals = useAsync(getAllDeals, []);
  const activities = useAsync(useCallback(() => listActivities({ companyId: id }), [id]), [id]);
  const { owners, contacts } = useDirectory();

  const companyDeals = useMemo(
    () => (deals.status === "success" ? deals.data.items.filter((d) => d.company_id === id) : []),
    [deals, id],
  );
  const openValue = useMemo(
    () => companyDeals.filter((d) => isOpen(d.stage)).reduce((s, d) => s + d.value_minor, 0),
    [companyDeals],
  );
  const companyContacts = useMemo(() => contactsForCompany(contacts, id), [contacts, id]);

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/companies"
        className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to companies
      </Link>

      {company.status === "loading" ? (
        <Card className="p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="mt-3 h-4 w-40" />
        </Card>
      ) : null}
      {company.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={company.error.message} />
        </Card>
      ) : null}

      {company.status === "success"
        ? (() => {
            const c = company.data;
            const owner = ownerById(owners, c.owner_id);
            return (
              <>
                <Card className="flex flex-col gap-4 p-6">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex flex-col gap-2">
                      <CompanyStatusPill status={c.status} />
                      <h1 className="display text-3xl font-extrabold leading-tight">{c.name}</h1>
                      <div className="flex flex-wrap items-center gap-4 text-sm text-muted">
                        <span className="inline-flex items-center gap-1.5">
                          <GlobeIcon className="size-4 text-faint" />
                          {c.domain}
                        </span>
                        <span className="inline-flex items-center gap-1.5">
                          <MapPinIcon className="size-4 text-faint" />
                          {c.city}, {c.country}
                        </span>
                        <span>{c.industry}</span>
                      </div>
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="display text-[2rem] font-extrabold leading-none tnum">
                        {formatMoneyCompact(openValue, c.currency)}
                      </span>
                      <span className="text-sm text-muted">open pipeline</span>
                    </div>
                  </div>
                </Card>

                <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 items-start">
                  <div className="flex flex-col gap-6 lg:col-span-2">
                    <Card className="flex flex-col gap-4 p-6">
                      <SectionHeader
                        title="Deals"
                        subtitle={`${companyDeals.length} in this account`}
                      />
                      {deals.status === "loading" ? (
                        <div className="flex flex-col gap-2">
                          {Array.from({ length: 3 }).map((_, i) => (
                            <Skeleton key={i} className="h-14 w-full rounded-tile" />
                          ))}
                        </div>
                      ) : companyDeals.length > 0 ? (
                        <div className="flex flex-col gap-2">
                          {companyDeals.map((d) => (
                            <DealRow key={d.id} deal={d} />
                          ))}
                        </div>
                      ) : (
                        <EmptyState
                          icon={<HandshakeIcon className="size-6" />}
                          title="No deals yet"
                          description="This account has no deals in the pipeline. Create one to get started."
                        />
                      )}
                    </Card>

                    <Card className="flex flex-col gap-5 p-6">
                      <SectionHeader title="Activity" subtitle="Recent touches with this account" />
                      {activities.status === "success" ? (
                        activities.data.length > 0 ? (
                          <ActivityTimeline activities={activities.data} showCompany={false} />
                        ) : (
                          <EmptyState
                            icon={<ActivityIcon className="size-6" />}
                            title="No activity yet"
                            description="Log a call, email or meeting to start this account's timeline."
                          />
                        )
                      ) : (
                        <div className="flex flex-col gap-4">
                          {Array.from({ length: 3 }).map((_, i) => (
                            <Skeleton key={i} className="h-14 w-full rounded-tile" />
                          ))}
                        </div>
                      )}
                    </Card>
                  </div>

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
                        <Fact label="Industry">{c.industry}</Fact>
                        <Fact label="Headcount">{c.size_band}</Fact>
                        <Fact label="Annual revenue">
                          <span className="tnum">{formatMoney(c.annual_revenue_minor, c.currency)}</span>
                        </Fact>
                        <Fact label="Customer since">
                          <span className="tnum">{formatDate(c.created_at)}</span>
                        </Fact>
                      </div>
                    </Card>

                    <Card className="flex flex-col gap-4 p-6">
                      <SectionHeader title="Contacts" subtitle={`${companyContacts.length} people`} />
                      <div className="flex flex-col gap-2">
                        {companyContacts.map((contact) => (
                          <div
                            key={contact.id}
                            className="flex items-center gap-3 rounded-tile bg-ground/60 px-3 py-2.5"
                          >
                            <Avatar name={contact.full_name} size="sm" />
                            <div className="flex min-w-0 flex-1 flex-col leading-tight">
                              <span className="truncate text-sm font-semibold text-ink">
                                {contact.full_name}
                              </span>
                              <span className="truncate text-xs text-muted">{contact.title}</span>
                            </div>
                            <ContactStatusPill status={contact.status} />
                          </div>
                        ))}
                      </div>
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
