import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

import { ArrowLeftIcon, InboxIcon } from "@opsgrids/ui";

import { listConversations } from "@/api/client";
import type { ConversationOut, ConversationStatus } from "@/api/types";
import { usePolling } from "@/lib/usePolling";
import {
  Announcer,
  Card,
  ConversationStatusTag,
  EmptyState,
  LoadFailure,
  PageHeader,
  Select,
  Skeleton,
  WindowStatusTag,
} from "@/ui";
import { ConversationThread } from "./ConversationThread";

/** Every conversation this screen offers the switch for. `undefined` fetches both. */
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "All conversations" },
  { value: "open", label: "Open" },
  { value: "closed", label: "Closed" },
];

/** Slow enough that an inbox left open all day is not a background tab hammering the gateway,
 *  fast enough that a new customer message shows up without anyone refreshing the page. */
const LIST_POLL_MS = 15_000;

function relativeSummary(conversation: ConversationOut): string {
  if (conversation.last_customer_message_at === null) return "No messages yet";
  return new Date(conversation.last_customer_message_at).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ConversationListItem({
  conversation,
  selected,
  onSelect,
}: {
  conversation: ConversationOut;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className={`flex w-full flex-col gap-1 rounded-tile border px-3 py-2.5 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
          selected
            ? "border-accent bg-accent/10"
            : "border-transparent hover:border-line hover:bg-surface-muted"
        }`}
      >
        <span className="flex items-center justify-between gap-2">
          <span className="truncate font-mono text-sm font-medium text-ink">
            {conversation.wa_address}
          </span>
          <ConversationStatusTag status={conversation.status} />
        </span>
        <span className="flex items-center justify-between gap-2 text-xs text-muted">
          <span>{relativeSummary(conversation)}</span>
          <WindowStatusTag withinWindow={conversation.within_service_window} />
        </span>
      </button>
    </li>
  );
}

const NO_CONVERSATIONS: ConversationOut[] = [];

function Inbox() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("id");
  const statusFilter = (params.get("status") ?? "") as ConversationStatus | "";

  const state = usePolling(
    () =>
      listConversations(statusFilter === "" ? {} : { status: statusFilter }),
    [statusFilter],
    LIST_POLL_MS,
  );
  const conversations = state.status === "success" ? state.data.items : NO_CONVERSATIONS;
  const selected = conversations.find((c) => c.id === selectedId) ?? null;

  // A conversation that closes, or drops off a status filter, should not leave the thread pane
  // pointed at a row the list no longer shows.
  useEffect(() => {
    if (state.status !== "success") return;
    if (selectedId !== null && selected === null && conversations.length > 0) {
      const next = new URLSearchParams(params);
      next.delete("id");
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status, selectedId, selected, conversations.length]);

  function select(id: string | null) {
    const next = new URLSearchParams(params);
    if (id === null) next.delete("id");
    else next.set("id", id);
    setParams(next, { replace: true });
  }

  function setStatus(value: string) {
    const next = new URLSearchParams(params);
    if (value === "") next.delete("status");
    else next.set("status", value);
    setParams(next, { replace: true });
  }

  return (
    <div className="rise flex h-full flex-col gap-6">
      <PageHeader
        eyebrow="Engage"
        title="Inbox"
        description="Two-way WhatsApp conversations with your customers, inside the 24-hour service window Meta allows."
      />

      <div className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-[22rem_1fr]">
        <Card
          className={`flex flex-col gap-3 p-4 ${selectedId !== null ? "hidden lg:flex" : "flex"}`}
        >
          <Select
            label="Filter by status"
            hideLabel
            value={statusFilter}
            onChange={(event) => setStatus(event.target.value)}
            options={STATUS_OPTIONS}
          />

          {state.status === "loading" ? (
            <div className="flex flex-col gap-2">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          ) : null}

          {state.status === "error" ? (
            <LoadFailure error={state.error} title="Couldn't load your conversations" />
          ) : null}

          {state.status === "success" && conversations.length === 0 ? (
            <EmptyState
              icon={<InboxIcon className="size-6" />}
              title="No conversations yet"
              description="A conversation appears here the first time a customer messages your WhatsApp number."
            />
          ) : null}

          {conversations.length > 0 ? (
            <ul className="flex flex-col gap-1.5 overflow-y-auto">
              {conversations.map((conversation) => (
                <ConversationListItem
                  key={conversation.id}
                  conversation={conversation}
                  selected={conversation.id === selectedId}
                  onSelect={() => select(conversation.id)}
                />
              ))}
            </ul>
          ) : null}
        </Card>

        <div className={selectedId === null ? "hidden lg:block" : "block"}>
          {selected !== null ? (
            <>
              <button
                type="button"
                onClick={() => select(null)}
                className="mb-3 inline-flex items-center gap-1.5 text-sm font-medium text-muted hover:text-ink lg:hidden"
              >
                <ArrowLeftIcon className="size-4" aria-hidden />
                Back to conversations
              </button>
              <ConversationThread conversation={selected} onChanged={state.refresh} />
            </>
          ) : (
            <Card className="hidden h-full items-center justify-center p-6 lg:flex">
              <EmptyState
                icon={<InboxIcon className="size-6" />}
                title="Select a conversation"
                description="Choose a conversation on the left to read its thread and reply."
              />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

/** The screen's single polite live region — the composer and the triage actions announce their
 *  result into it. */
export function InboxPage() {
  return (
    <Announcer>
      <Inbox />
    </Announcer>
  );
}
