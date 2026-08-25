import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getTicket, listAgents } from "@/api/client";
import type { Agent, Message, Ticket } from "@/api/types";
import { useAuth } from "@atmora/client";
import { agentById } from "@/data/derive";
import { ChevronRightIcon, MailIcon, PaperclipIcon, ReplyIcon, cn } from "@atmora/ui";
import { channelLabel, formatDateTime, initials } from "@/lib/format";
import { useAsync } from "@atmora/client";
import {
  Avatar,
  Button,
  Card,
  ErrorState,
  PriorityPill,
  SectionHeader,
  Skeleton,
  SlaPill,
  Tag,
  TicketStatusPill,
} from "@/ui";

/** One message in the thread — styled by who wrote it, with internal notes clearly set apart. */
function MessageBubble({ message }: { message: Message }) {
  if (message.author_type === "system") {
    return (
      <li className="flex items-center gap-3 py-1 text-xs text-faint">
        <span className="h-px flex-1 bg-line" aria-hidden />
        <span>{message.body}</span>
        <span className="h-px flex-1 bg-line" aria-hidden />
      </li>
    );
  }

  const isAgent = message.author_type === "agent";
  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        {isAgent ? (
          <span className="grid size-8 place-items-center rounded-full bg-accent text-xs font-bold text-white">
            {initials(message.author_name)}
          </span>
        ) : (
          <Avatar name={message.author_name} size="sm" />
        )}
        <span className="text-sm font-semibold text-ink">{message.author_name}</span>
        <span className="text-xs font-medium text-faint">
          {isAgent ? "Agent" : "Customer"}
        </span>
        {message.internal ? (
          <span className="rounded-full bg-late-soft px-2 py-0.5 text-[0.7rem] font-bold text-late">
            Internal note
          </span>
        ) : null}
        <span className="ml-auto text-xs text-faint tnum">{formatDateTime(message.at)}</span>
      </div>
      <div
        className={cn(
          "rounded-card px-4 py-3 text-sm leading-relaxed ml-10",
          message.internal
            ? "border border-late/30 bg-late-soft/50 text-ink-soft"
            : isAgent
              ? "bg-accent-soft text-ink"
              : "bg-ground text-ink-soft",
        )}
      >
        {message.body}
      </div>
    </li>
  );
}

/** A labelled property row in the sidebar. */
function PropertyRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-sm text-muted">{label}</span>
      <div className="text-right">{children}</div>
    </div>
  );
}

function TicketProperties({ ticket, assignee }: { ticket: Ticket; assignee: Agent | null }) {
  return (
    <Card className="flex flex-col gap-1 p-6">
      <SectionHeader title="Properties" />

      <div className="mt-1 flex items-center gap-3 rounded-tile bg-ground p-3">
        <Avatar name={ticket.requester.name} size="md" />
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-sm font-bold text-ink">{ticket.requester.name}</span>
          <span className="truncate text-xs text-muted">{ticket.requester.email}</span>
        </div>
        <span
          className={cn(
            "ml-auto rounded-full px-2 py-0.5 text-[0.7rem] font-bold",
            ticket.requester.kind === "staff"
              ? "bg-accent-soft text-accent-ink"
              : "bg-info-soft text-info",
          )}
        >
          {ticket.requester.kind === "staff" ? "Staff" : "Customer"}
        </span>
      </div>

      <div className="mt-2 divide-y divide-line/70">
        <PropertyRow label="Company">
          <span className="text-sm font-semibold text-ink">{ticket.requester.company}</span>
        </PropertyRow>
        <PropertyRow label="Channel">
          <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink-soft">
            <MailIcon className="size-4 text-faint" />
            {channelLabel(ticket.channel)}
          </span>
        </PropertyRow>
        <PropertyRow label="Priority">
          <PriorityPill priority={ticket.priority} />
        </PropertyRow>
        <PropertyRow label="Status">
          <TicketStatusPill status={ticket.status} />
        </PropertyRow>
        <PropertyRow label="Assignee">
          {assignee ? (
            <span className="inline-flex items-center gap-2">
              <Avatar name={assignee.name} size="sm" />
              <span className="text-sm font-semibold text-ink">{assignee.name}</span>
            </span>
          ) : (
            <span className="text-sm font-medium text-faint">Unassigned</span>
          )}
        </PropertyRow>
        <PropertyRow label="SLA">
          <SlaPill ticket={ticket} />
        </PropertyRow>
        <PropertyRow label="Opened">
          <span className="text-sm text-ink-soft tnum">{formatDateTime(ticket.created_at)}</span>
        </PropertyRow>
        <PropertyRow label="First response">
          <span className="text-sm text-ink-soft tnum">
            {formatDateTime(ticket.first_response_at)}
          </span>
        </PropertyRow>
      </div>

      <div className="mt-2 flex flex-col gap-2 border-t border-line pt-4">
        <span className="text-xs font-semibold uppercase tracking-wide text-faint">Tags</span>
        <div className="flex flex-wrap gap-1.5">
          {ticket.tags.map((tag) => (
            <Tag key={tag}>{tag}</Tag>
          ))}
        </div>
      </div>
    </Card>
  );
}

/** The reply box — a real affordance: it appends to the thread (fixtures have no backend). */
function ReplyBox({
  authorName,
  onSend,
}: {
  authorName: string;
  onSend: (body: string, internal: boolean) => void;
}) {
  const [body, setBody] = useState("");
  const [internal, setInternal] = useState(false);
  const trimmed = body.trim();

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!trimmed) return;
    onSend(trimmed, internal);
    setBody("");
    setInternal(false);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <label htmlFor="reply-body" className="sr-only">
        Write a reply
      </label>
      <textarea
        id="reply-body"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        placeholder={
          internal
            ? "Add an internal note — visible to agents only…"
            : `Reply as ${authorName}…`
        }
        className={cn(
          "w-full resize-y rounded-tile border bg-surface px-4 py-3 text-sm text-ink placeholder:text-faint",
          "focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2",
          internal ? "border-late/40 bg-late-soft/30" : "border-line",
        )}
      />
      <div className="flex flex-wrap items-center gap-2">
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-line bg-surface px-3 h-9 text-sm font-semibold text-ink-soft">
          <input
            type="checkbox"
            checked={internal}
            onChange={(e) => setInternal(e.target.checked)}
            className="size-4 accent-accent"
          />
          Internal note
        </label>
        <button
          type="button"
          className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-sm font-semibold text-ink-soft hover:bg-ground transition-colors"
        >
          <PaperclipIcon className="size-4" />
          Attach
        </button>
        <Button
          type="submit"
          size="sm"
          disabled={!trimmed}
          leadingIcon={<ReplyIcon className="size-4" />}
          className="ml-auto"
        >
          {internal ? "Add note" : "Send reply"}
        </Button>
      </div>
    </form>
  );
}

export function TicketDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const loader = useCallback(() => getTicket(id ?? ""), [id]);
  const ticketState = useAsync(loader, [id]);
  const agentsState = useAsync(listAgents, []);

  const [appended, setAppended] = useState<Message[]>([]);

  const agents = agentsState.status === "success" ? agentsState.data : [];
  const ticket = ticketState.status === "success" ? ticketState.data : null;

  const thread = useMemo(
    () => (ticket ? [...ticket.messages, ...appended] : []),
    [ticket, appended],
  );

  const handleSend = useCallback(
    (body: string, internal: boolean) => {
      const message: Message = {
        id: `local-${appended.length + 1}`,
        author_type: "agent",
        author_name: user?.full_name ?? "You",
        at: new Date().toISOString(),
        body,
        ...(internal ? { internal: true } : {}),
      };
      setAppended((prev) => [...prev, message]);
    },
    [appended.length, user],
  );

  return (
    <div className="rise flex flex-col gap-5">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm font-medium">
        <Link to="/queue" className="text-muted hover:text-ink transition-colors">
          Queue
        </Link>
        <ChevronRightIcon className="size-4 text-faint" />
        <span className="text-ink-soft tnum">{id}</span>
      </nav>

      {ticketState.status === "loading" ? (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <Card className="p-6 lg:col-span-2">
            <div className="flex flex-col gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full rounded-tile" />
              ))}
            </div>
          </Card>
          <Card className="p-6">
            <Skeleton className="h-72 w-full rounded-tile" />
          </Card>
        </div>
      ) : null}

      {ticketState.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={ticketState.error.message} />
          <div className="mt-4 flex justify-center">
            <Link
              to="/queue"
              className="rounded-full bg-ink px-5 h-11 inline-flex items-center font-semibold text-white hover:bg-ink-soft transition-colors"
            >
              Back to queue
            </Link>
          </div>
        </Card>
      ) : null}

      {ticket ? (
        <>
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex min-w-0 flex-col gap-2">
              <h1 className="display text-2xl font-extrabold leading-tight sm:text-[1.9rem]">
                {ticket.subject}
              </h1>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-muted tnum">{ticket.id}</span>
                <span className="text-faint">·</span>
                <PriorityPill priority={ticket.priority} />
                <TicketStatusPill status={ticket.status} />
                <SlaPill ticket={ticket} />
              </div>
            </div>
          </header>

          <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-3">
            {/* Conversation */}
            <Card className="flex flex-col gap-5 p-6 lg:col-span-2">
              <SectionHeader
                title="Conversation"
                subtitle={`${thread.length} message${thread.length === 1 ? "" : "s"}`}
              />
              <ul className="flex flex-col gap-5" aria-live="polite">
                {thread.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
              </ul>
              <div className="border-t border-line pt-5">
                <ReplyBox authorName={user?.full_name ?? "You"} onSend={handleSend} />
              </div>
            </Card>

            {/* Properties */}
            <TicketProperties ticket={ticket} assignee={agentById(agents, ticket.assignee_id)} />
          </div>
        </>
      ) : null}
    </div>
  );
}
