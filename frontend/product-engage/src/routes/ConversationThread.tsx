import { useEffect, useRef, useState } from "react";

import { Can, useAsync } from "@opsgrids/client";
import {
  CheckCircleIcon,
  ClockIcon,
  RefreshIcon,
  SendIcon,
  UserIcon,
  formatDuration,
} from "@opsgrids/ui";

import {
  closeConversation,
  listConversationMessages,
  listWhatsAppTemplates,
  replyToConversation,
} from "@/api/client";
import type { ConversationMessageOut, ConversationOut } from "@/api/types";
import { CRM_CONTACTS_URL } from "@/lib/dashboard";
import { failureMessage, replyRemedy } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import {
  Button,
  Card,
  EmptyState,
  InlineAlert,
  LoadFailure,
  Skeleton,
  TextArea,
  WindowStatusTag,
  useAnnounce,
} from "@/ui";

/** Meta's customer service window, mirrored from `webhooks.SERVICE_WINDOW` — the composer needs
 *  the deadline to count down to, and the service sends back only the boolean it decided from it. */
const SERVICE_WINDOW_MS = 24 * 60 * 60 * 1000;

/** Faster than the conversation list's poll: an open thread is what someone is actively
 *  watching, and eight seconds is a customer-service-chat cadence rather than an inbox-summary
 *  one. */
const THREAD_POLL_MS = 8_000;

function windowDeadline(conversation: ConversationOut): Date | null {
  if (conversation.last_customer_message_at === null) return null;
  return new Date(new Date(conversation.last_customer_message_at).getTime() + SERVICE_WINDOW_MS);
}

function MessageBubble({ message }: { message: ConversationMessageOut }) {
  const mine = message.direction === "outbound";
  return (
    <li className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[75%] rounded-tile px-4 py-2 text-sm shadow-sm ${
          mine ? "bg-accent text-white" : "border border-line bg-surface text-ink"
        }`}
      >
        {!mine && message.profile_name !== null ? (
          <p className="mb-1 text-xs font-semibold opacity-80">{message.profile_name}</p>
        ) : null}
        <p className="whitespace-pre-wrap break-words">{message.body}</p>
        <p className={`mt-1 text-[0.7rem] ${mine ? "text-white/75" : "text-faint"}`}>
          {formatInstant(message.occurred_at)}
        </p>
      </div>
    </li>
  );
}

/** Ticks once a minute — a countdown that redrew every second would be motion with no orientation
 *  value, which `prefers-reduced-motion` and this platform's own motion rule both rule out. */
function Countdown({ deadline }: { deadline: Date }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(timer);
  }, []);
  const remainingMs = deadline.getTime() - now.getTime();
  if (remainingMs <= 0) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted">
      <ClockIcon className="size-3.5" aria-hidden />
      {formatDuration(remainingMs)} left to reply freely
    </span>
  );
}

/**
 * What the composer becomes once the service window has closed. There is no send action here:
 * `POST /engage/conversations/{id}/reply` only ever accepts free text, and this slice does not add
 * a second endpoint that resolves a template send against the conversation's sealed number — so
 * this is deliberately a read of what is approved and ready, not a button that would do nothing.
 */
function TemplatePicker({ conversation }: { conversation: ConversationOut }) {
  const state = useAsync(() => listWhatsAppTemplates(), []);
  const approved =
    state.status === "success" ? state.data.items.filter((template) => template.sendable) : [];

  return (
    <div className="flex flex-col gap-3 rounded-tile border border-line bg-surface-muted p-4">
      <p className="text-sm text-ink">
        This conversation&rsquo;s 24-hour window has closed. WhatsApp itself refuses free text
        outside it — only an approved template can reach {conversation.wa_address} now.
      </p>

      {state.status === "loading" ? <Skeleton className="h-16 w-full" /> : null}
      {state.status === "error" ? (
        <p className="text-sm text-negative" role="alert">
          Couldn&rsquo;t load your approved templates: {state.error.message}
        </p>
      ) : null}
      {state.status === "success" && approved.length === 0 ? (
        <p className="text-sm text-muted">
          No approved WhatsApp template yet — submit one from WhatsApp → Templates.
        </p>
      ) : null}
      {approved.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {approved.map((template) => (
            <li key={template.id} className="rounded-tile border border-line bg-surface p-3">
              <p className="text-sm font-semibold text-ink">{template.provider_name}</p>
              <p className="text-sm text-muted">{template.preview_text}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Composer({
  conversation,
  onSent,
}: {
  conversation: ConversationOut;
  onSent: (state: string) => void;
}) {
  const [body, setBody] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // Starts from the server's own answer, and a `409` mid-send flips it locally: a webhook can
  // close the window between this screen loading and the click landing, and the composer should
  // not need a whole poll cycle to catch up with the one response that just told it so.
  const [closedLocally, setClosedLocally] = useState(false);

  // One key per draft, not per click: a network failure that actually reached the server (the
  // response was what got lost, not the request) must resolve on retry to the *same* message,
  // never a second one. Cleared on a successful send and whenever the draft is edited, so a
  // changed message is honestly a new one — only a bare retry of the exact text just refused
  // reuses the key that refusal already carried.
  const idempotencyKeyRef = useRef<string | null>(null);
  const lastKeyedBodyRef = useRef<string>("");

  const windowClosed = closedLocally || !conversation.within_service_window;

  useEffect(() => {
    setClosedLocally(false);
  }, [conversation.id, conversation.within_service_window]);

  if (windowClosed) return <TemplatePicker conversation={conversation} />;

  const deadline = windowDeadline(conversation);

  function keyFor(text: string): string {
    if (idempotencyKeyRef.current === null || lastKeyedBodyRef.current !== text) {
      idempotencyKeyRef.current = crypto.randomUUID();
      lastKeyedBodyRef.current = text;
    }
    return idempotencyKeyRef.current;
  }

  async function submit() {
    const text = body.trim();
    if (pending || text === "") return;
    setPending(true);
    setFailure(null);
    try {
      const result = await replyToConversation(conversation.id, {
        body: text,
        idempotency_key: keyFor(text),
      });
      idempotencyKeyRef.current = null;
      setBody("");
      onSent(result.state);
    } catch (caught) {
      if (replyRemedy(caught) === "template") {
        setClosedLocally(true);
      } else {
        setFailure(failureMessage(caught));
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <Can
      permission="engage.inbox.message.reply"
      scope="ORG"
      fallback={
        <p className="text-sm text-muted">
          You don&rsquo;t have permission to reply in this inbox.
        </p>
      }
    >
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {failure !== null ? (
          <InlineAlert tone="danger" title="That reply wasn't sent">
            {failure}
          </InlineAlert>
        ) : null}
        <TextArea
          label="Reply"
          hideLabel
          placeholder="Type a reply…"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          rows={2}
          maxLength={4096}
          disabled={pending}
        />
        <div className="flex items-center justify-between gap-3">
          {deadline !== null ? <Countdown deadline={deadline} /> : <span />}
          <Button
            type="submit"
            disabled={pending || body.trim() === ""}
            leadingIcon={<SendIcon className="size-4" />}
          >
            {pending ? "Sending…" : "Send"}
          </Button>
        </div>
      </form>
    </Can>
  );
}

/**
 * One conversation: its thread, its composer, and the two triage actions. Polls the thread on its
 * own cadence rather than sharing the list's — a reply that just landed should not wait on the
 * list's slower interval, and the list does not need every message to redraw itself.
 */
export function ConversationThread({
  conversation,
  onChanged,
}: {
  conversation: ConversationOut;
  onChanged: () => void;
}) {
  const announce = useAnnounce();
  const state = usePolling(
    () => listConversationMessages(conversation.id, { size: 200 }),
    [conversation.id],
    THREAD_POLL_MS,
  );
  const [actionFailure, setActionFailure] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState(false);

  // There is no `POST .../reopen`: a closed conversation reopens itself the moment either side
  // writes to it again — a reply from here, or the customer's next message through the webhook —
  // so the only explicit action this screen offers is closing one that is still open.
  async function close() {
    if (actionPending) return;
    setActionPending(true);
    setActionFailure(null);
    try {
      await closeConversation(conversation.id);
      announce("Conversation closed.");
      onChanged();
    } catch (caught) {
      setActionFailure(failureMessage(caught));
    } finally {
      setActionPending(false);
    }
  }

  return (
    <Card className="flex h-full flex-col gap-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-ink">{conversation.wa_address}</span>
          <WindowStatusTag withinWindow={conversation.within_service_window} />
        </div>
        {conversation.status === "open" ? (
          <Can permission="engage.inbox.conversation.write" scope="ORG">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void close()}
              disabled={actionPending}
              leadingIcon={<CheckCircleIcon className="size-4" />}
            >
              Close
            </Button>
          </Can>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted">
            <RefreshIcon className="size-3.5" aria-hidden />
            Closed — a reply reopens it
          </span>
        )}
      </div>

      {conversation.contact_id !== null ? (
        <a
          href={CRM_CONTACTS_URL}
          target="_blank"
          rel="noreferrer"
          className="inline-flex w-fit items-center gap-1.5 text-xs font-medium text-accent underline decoration-1 underline-offset-2 hover:text-accent-strong"
        >
          <UserIcon className="size-3.5" aria-hidden />
          Matched to a CRM contact — view in Revenue
        </a>
      ) : (
        <p className="text-xs text-faint">No CRM contact matched this number yet.</p>
      )}

      {actionFailure !== null ? (
        <InlineAlert tone="danger" title="That didn't go through">
          {actionFailure}
        </InlineAlert>
      ) : null}

      <div className="flex-1 overflow-y-auto" aria-live="polite" aria-atomic="false">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-12 w-2/3" />
            <Skeleton className="ml-auto h-12 w-2/3" />
          </div>
        ) : null}
        {state.status === "error" ? (
          <LoadFailure error={state.error} title="Couldn't load this conversation" />
        ) : null}
        {state.status === "success" && state.data.items.length === 0 ? (
          <EmptyState
            icon={<ClockIcon className="size-6" />}
            title="No messages yet"
            description="Nothing has been exchanged in this conversation."
          />
        ) : null}
        {state.status === "success" && state.data.items.length > 0 ? (
          <ol className="flex flex-col gap-3">
            {state.data.items.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
          </ol>
        ) : null}
      </div>

      <Composer
        conversation={conversation}
        onSent={(sentState) => {
          announce(
            sentState === "suppressed"
              ? "Recorded, but not sent — this customer has opted out of WhatsApp messages."
              : "Reply sent.",
          );
          state.refresh();
        }}
      />
    </Card>
  );
}
