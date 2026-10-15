import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { deleteCompany, getCompany, listActivities, listContacts, listDeals } from "@/api/client";
import { announceDealsChanged } from "@/data/dealsChanged";
import { useDirectory } from "@/data/directoryContext";
import { ownerById } from "@/data/lookups";
import { isOpen } from "@/data/stages";
import { formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  ActivityTimeline,
  Avatar,
  Button,
  Card,
  CompanyStatusPill,
  ConfirmDialog,
  ContactStatusPill,
  EmptyState,
  ErrorState,
  SectionHeader,
  Skeleton,
  StagePill,
} from "@/ui";
import type { Deal } from "@/api/types";
import {
  ActivityIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  GlobeIcon,
  HandshakeIcon,
  MapPinIcon,
  useToast,
  formatMoney,
} from "@opsgrids/ui";
import { CompanyFormDialog } from "@/forms/CompanyFormDialog";
import { ContactFormDialog } from "@/forms/ContactFormDialog";
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
  const navigate = useNavigate();
  const toast = useToast();
  const directory = useDirectory();
  const { owners, companies, contacts: allContacts } = directory;
  const canWriteContact = useCan("crm.book.contact.write", "OWN");
  const canWriteDeal = useCan("crm.pipeline.deal.write", "OWN");
  const canLog = useCan("crm.pipeline.activity.write", "OWN");

  const company = useAsync(useCallback(() => getCompany(id), [id]), [id]);
  const deals = useAsync(useCallback(() => listDeals({ companyId: id, size: 100 }), [id]), [id]);
  const contacts = useAsync(useCallback(() => listContacts({ companyId: id, size: 100 }), [id]), [id]);
  const activities = useAsync(
    useCallback(() => listActivities({ companyId: id }), [id]),
    [id],
  );

  const [editingCompany, setEditingCompany] = useState(false);
  const [deletingCompany, setDeletingCompany] = useState(false);
  const [creatingContact, setCreatingContact] = useState(false);
  const [creatingDeal, setCreatingDeal] = useState(false);
  const [logging, setLogging] = useState(false);

  const companyDeals = useMemo(
    () => (deals.status === "success" ? deals.data.items : []),
    [deals],
  );
  const openValue = useMemo(
    () => companyDeals.filter((d) => isOpen(d.stage)).reduce((s, d) => s + d.value_minor, 0),
    [companyDeals],
  );
  const companyContacts = useMemo(
    () => (contacts.status === "success" ? contacts.data.items : []),
    [contacts],
  );

  const remove = useMutation(() => deleteCompany(id), {
    onSuccess: () => {
      toast.success("Company deleted.");
      directory.reload();
      navigate("/companies");
    },
  });

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/companies"
        className="relative hit-44 inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
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
          <ErrorState
            title="Couldn't load this company"
            message={problemMessage(company.error)}
            onRetry={company.reload}
          />
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
                    <div className="flex flex-col items-end gap-3">
                      <div className="flex flex-col items-end">
                        <span className="display text-[2rem] font-extrabold leading-none tnum">
                          {formatMoney(openValue, c.currency, { compact: true })}
                        </span>
                        <span className="text-sm text-muted">open pipeline</span>
                      </div>
                      <Can permission="crm.book.company.write" scope="OWN">
                        <div className="flex items-center gap-2">
                          <Button size="sm" variant="outline" onClick={() => setEditingCompany(true)}>
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-negative"
                            onClick={() => setDeletingCompany(true)}
                          >
                            Delete
                          </Button>
                        </div>
                      </Can>
                    </div>
                  </div>
                </Card>

                <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 items-start">
                  <div className="flex flex-col gap-6 lg:col-span-2">
                    <Card className="flex flex-col gap-4 p-6">
                      <SectionHeader
                        title="Deals"
                        subtitle={`${companyDeals.length} in this account`}
                        action={
                          <Can permission="crm.pipeline.deal.write" scope="OWN">
                            <Button size="sm" variant="outline" onClick={() => setCreatingDeal(true)}>
                              New deal
                            </Button>
                          </Can>
                        }
                      />
                      {deals.status === "loading" ? (
                        <div className="flex flex-col gap-2">
                          {Array.from({ length: 3 }).map((_, i) => (
                            <Skeleton key={i} className="h-14 w-full rounded-tile" />
                          ))}
                        </div>
                      ) : null}
                      {deals.status === "error" ? (
                        <ErrorState message={problemMessage(deals.error)} onRetry={deals.reload} />
                      ) : null}
                      {deals.status === "success" ? (
                        companyDeals.length > 0 ? (
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
                            action={
                              canWriteDeal ? (
                                <Button size="sm" onClick={() => setCreatingDeal(true)}>
                                  New deal
                                </Button>
                              ) : null
                            }
                          />
                        )
                      ) : null}
                    </Card>

                    <Card className="flex flex-col gap-5 p-6">
                      <SectionHeader
                        title="Activity"
                        subtitle="Recent touches with this account"
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
                          {Array.from({ length: 3 }).map((_, i) => (
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
                        activities.data.items.length > 0 ? (
                          <ActivityTimeline activities={activities.data.items} showCompany={false} />
                        ) : (
                          <EmptyState
                            icon={<ActivityIcon className="size-6" />}
                            title="No activity yet"
                            description="Log a call, email or meeting to start this account's timeline."
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
                      <SectionHeader
                        title="Contacts"
                        subtitle={`${companyContacts.length} people`}
                        action={
                          <Can permission="crm.book.contact.write" scope="OWN">
                            <Button size="sm" variant="outline" onClick={() => setCreatingContact(true)}>
                              New contact
                            </Button>
                          </Can>
                        }
                      />
                      {contacts.status === "loading" ? (
                        <div className="flex flex-col gap-2">
                          {Array.from({ length: 3 }).map((_, i) => (
                            <Skeleton key={i} className="h-12 w-full rounded-tile" />
                          ))}
                        </div>
                      ) : null}
                      {contacts.status === "error" ? (
                        <ErrorState message={problemMessage(contacts.error)} onRetry={contacts.reload} />
                      ) : null}
                      {contacts.status === "success" ? (
                        companyContacts.length > 0 ? (
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
                        ) : (
                          <EmptyState
                            title="No contacts yet"
                            description="Add the first person at this account."
                            action={
                              canWriteContact ? (
                                <Button size="sm" onClick={() => setCreatingContact(true)}>
                                  New contact
                                </Button>
                              ) : null
                            }
                          />
                        )
                      ) : null}
                    </Card>
                  </div>
                </div>

                {editingCompany ? (
                  <CompanyFormDialog
                    company={c}
                    owners={owners}
                    onOwnerCreated={() => directory.reload()}
                    onClose={() => setEditingCompany(false)}
                    onSaved={() => {
                      setEditingCompany(false);
                      toast.success(`${c.name} saved.`);
                      company.reload();
                      directory.reload();
                    }}
                  />
                ) : null}

                {creatingContact ? (
                  <ContactFormDialog
                    contact={null}
                    companies={companies}
                    owners={owners}
                    defaultCompanyId={c.id}
                    onOwnerCreated={() => directory.reload()}
                    onClose={() => setCreatingContact(false)}
                    onSaved={(contact) => {
                      setCreatingContact(false);
                      toast.success(`${contact.full_name} created.`);
                      contacts.reload();
                      directory.reload();
                    }}
                  />
                ) : null}

                {creatingDeal ? (
                  <DealFormDialog
                    deal={null}
                    companies={companies}
                    contacts={allContacts}
                    owners={owners}
                    defaultCompanyId={c.id}
                    onOwnerCreated={() => directory.reload()}
                    onClose={() => setCreatingDeal(false)}
                    onSaved={(dealRow) => {
                      setCreatingDeal(false);
                      toast.success(`${dealRow.name} created.`);
                      deals.reload();
                      announceDealsChanged();
                    }}
                  />
                ) : null}

                {logging ? (
                  <ActivityFormDialog
                    context={{ companyId: c.id, label: c.name }}
                    onClose={() => setLogging(false)}
                    onSaved={() => {
                      setLogging(false);
                      toast.success("Activity logged.");
                      activities.reload();
                    }}
                  />
                ) : null}

                <ConfirmDialog
                  open={deletingCompany}
                  onClose={() => {
                    setDeletingCompany(false);
                    remove.reset();
                  }}
                  onConfirm={() => void remove.mutate(undefined)}
                  title="Delete this company?"
                  body={`${c.name} is removed from the account book. This can't be undone.`}
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
