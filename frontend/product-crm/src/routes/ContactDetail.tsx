import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";

import { getContact } from "@/api/client";
import { MessagingCard } from "@/ui/MessagingCard";
import { Card, ContactStatusPill, ErrorState, Skeleton } from "@/ui";
import { problemMessage, useAsync } from "@opsgrids/client";
import { ArrowLeftIcon, ArrowRightIcon } from "@opsgrids/ui";

/** A contact's own page: the home of the Messaging card, and where Engage links a matched conversation. */
export function ContactDetailPage() {
  const { id = "" } = useParams();
  const contact = useAsync(useCallback(() => getContact(id), [id]), [id]);
  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/contacts"
        className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to contacts
      </Link>
      {contact.status === "loading" ? (
        <Card className="p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="mt-3 h-4 w-40" />
        </Card>
      ) : null}
      {contact.status === "error" ? (
        <Card className="p-6">
          <ErrorState title="Couldn't load this contact" message={problemMessage(contact.error)} onRetry={contact.reload} />
        </Card>
      ) : null}
      {contact.status === "success" ? (
        <>
          <Card className="flex flex-col items-start gap-3 p-4 sm:p-6">
            <ContactStatusPill status={contact.data.status} />
            <h1 className="display text-2xl font-extrabold leading-tight sm:text-3xl">{contact.data.full_name}</h1>
            {contact.data.title !== "" ? <p className="text-sm text-muted">{contact.data.title}</p> : null}
            <Link
              to={`/companies/${contact.data.company_id}`}
              className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
            >
              View company
              <ArrowRightIcon className="size-4" />
            </Link>
          </Card>
          <MessagingCard contactId={contact.data.id} />
        </>
      ) : null}
    </div>
  );
}
