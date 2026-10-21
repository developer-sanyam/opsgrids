import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  CheckCircleIcon,
  MessageSquareIcon,
  SendIcon,
  SparkIcon,
} from "@opsgrids/ui";

import { connectWhatsApp, getWhatsAppConnection, replaceWhatsApp } from "@/api/client";
import type { WhatsAppConnectionIn, WhatsAppConnectionOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import {
  Announcer,
  Button,
  Card,
  ConnectionStatusTag,
  InlineAlert,
  LoadFailure,
  PageHeader,
  Skeleton,
  TextArea,
  TextField,
  useAnnounce,
} from "@/ui";

/**
 * The organization's WhatsApp Cloud API account — connecting it, and replacing it once it
 * exists. One screen, no wizard steps: unlike email, WhatsApp has exactly one kind of provider,
 * so there is no first question to ask before the credential fields.
 *
 * **The access token is write-only and stays that way.** `WhatsAppConnectionOut` has no field
 * derived from it — not masked, not a length, not a last-four — because a hint of a token that
 * can send as this business is still a credential. The form never reads one back; it only ever
 * sends one, once, on submit.
 *
 * There is no template-approval promise here. Connecting an account records the credential the
 * platform uses to reach Meta; it does not and cannot make Meta approve anything, which is why
 * this screen has no "verified" language the way `ConnectionPanel`'s does for email — a WhatsApp
 * account is `connected` or it is not, and whether a message can actually go out is a question
 * the Templates screen answers, honestly, from what Meta has said.
 */

interface Values {
  wabaId: string;
  phoneNumberId: string;
  displayNumber: string;
  accessToken: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const EMPTY: Values = { wabaId: "", phoneNumberId: "", displayNumber: "", accessToken: "" };

function validate(values: Values): Errors {
  const errors: Errors = {};
  if (values.wabaId.trim() === "") {
    errors.wabaId = "The WhatsApp Business Account (WABA) ID from Meta Business Manager.";
  }
  if (values.phoneNumberId.trim() === "") {
    errors.phoneNumberId = "The phone number ID from Meta's WhatsApp Cloud API setup.";
  }
  if (values.accessToken === "") {
    errors.accessToken = "Paste the access token you generated for this app.";
  }
  return errors;
}

function toInput(values: Values): WhatsAppConnectionIn {
  return {
    waba_id: values.wabaId.trim(),
    phone_number_id: values.phoneNumberId.trim(),
    ...(values.displayNumber.trim() === "" ? {} : { display_number: values.displayNumber.trim() }),
    // Not trimmed, for the same reason the email connection's secret is not: a token whose first
    // character is a space is a token with a space in it.
    access_token: values.accessToken,
  };
}

function WhatsAppForm({
  mode,
  onSaved,
  onCancel,
}: {
  mode: "connect" | "replace";
  onSaved: (connection: WhatsAppConnectionOut) => void;
  onCancel?: () => void;
}) {
  const announce = useAnnounce();
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function submit() {
    if (submitting) return;
    const found = validate(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      const connection =
        mode === "replace"
          ? await replaceWhatsApp(toInput(values))
          : await connectWhatsApp(toInput(values));
      // The token has left with the request; this is the last moment anything on this side holds
      // it, and the field carrying it is cleared with everything else.
      setValues(EMPTY);
      announce(
        mode === "replace"
          ? "WhatsApp account replaced."
          : "WhatsApp account connected. Its templates still need Meta's approval before they can send.",
      );
      onSaved(connection);
    } catch (caught) {
      // Everything typed stays, the token included — it is still only in the field it was typed
      // into, and clearing it here would mean pasting a long-lived token again to retry.
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="flex flex-col gap-5 p-6">
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert
            tone="danger"
            title={mode === "replace" ? "The account wasn't replaced" : "That account wasn't connected"}
          >
            {failure}
          </InlineAlert>
        ) : null}
        {failure === null && Object.keys(errors).length > 0 ? (
          <InlineAlert tone="warning" title="Check the fields marked below">
            Nothing has been sent yet.
          </InlineAlert>
        ) : null}
      </div>

      <form
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label="WhatsApp Business Account ID"
            value={values.wabaId}
            onChange={(event) => set("wabaId", event.target.value)}
            placeholder="102938475610234"
            autoComplete="off"
            spellCheck={false}
            helper="From Meta Business Manager, under WhatsApp Accounts."
            disabled={submitting}
            {...(errors.wabaId !== undefined ? { error: errors.wabaId } : {})}
          />
          <TextField
            label="Phone number ID"
            value={values.phoneNumberId}
            onChange={(event) => set("phoneNumberId", event.target.value)}
            placeholder="109827364501928"
            autoComplete="off"
            spellCheck={false}
            helper="The sending number's ID, from the same setup page — not the phone number itself."
            disabled={submitting}
            {...(errors.phoneNumberId !== undefined ? { error: errors.phoneNumberId } : {})}
          />
        </div>

        <TextField
          label="Display number"
          value={values.displayNumber}
          onChange={(event) => set("displayNumber", event.target.value)}
          placeholder="+91 98765 43210"
          optional
          autoComplete="off"
          helper="The published number, for you to recognise this account by — not sent to Meta."
          disabled={submitting}
        />

        <TextField
          label="Access token"
          type="password"
          value={values.accessToken}
          onChange={(event) => set("accessToken", event.target.value)}
          autoComplete="new-password"
          helper="The permanent access token generated for this app in Meta Business Manager."
          disabled={submitting}
          {...(errors.accessToken !== undefined ? { error: errors.accessToken } : {})}
        />

        <InlineAlert tone="info" live="off" title="This token is never shown again">
          It is sealed when it is saved and read only by the server that calls the WhatsApp
          Cloud API. Nothing sends it back to this screen — not masked, not shortened, not
          counted.
        </InlineAlert>

        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={submitting}>
            {submitting
              ? mode === "replace"
                ? "Replacing…"
                : "Connecting…"
              : mode === "replace"
                ? "Replace account"
                : "Connect account"}
          </Button>
          {onCancel !== undefined ? (
            <Button variant="ghost" onClick={onCancel} disabled={submitting}>
              Cancel
            </Button>
          ) : null}
        </div>
      </form>
    </Card>
  );
}

function ConnectionPanel({
  connection,
  onReplace,
}: {
  connection: WhatsAppConnectionOut;
  onReplace: () => void;
}) {
  return (
    <Card className="flex flex-col gap-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-bold tracking-tight">
          {connection.display_number === "" ? "WhatsApp Business account" : connection.display_number}
        </h2>
        <ConnectionStatusTag status={connection.status} />
      </div>

      <dl className="flex flex-col">
        <div className="flex flex-col gap-1 border-t border-line py-3 first:border-t-0 first:pt-0 sm:flex-row sm:items-baseline sm:gap-4">
          <dt className="w-56 shrink-0 text-sm font-semibold text-muted">WABA ID</dt>
          <dd className="min-w-0 break-words font-mono text-sm tnum text-ink">{connection.waba_id}</dd>
        </div>
        <div className="flex flex-col gap-1 border-t border-line py-3 sm:flex-row sm:items-baseline sm:gap-4">
          <dt className="w-56 shrink-0 text-sm font-semibold text-muted">Phone number ID</dt>
          <dd className="min-w-0 break-words font-mono text-sm tnum text-ink">
            {connection.phone_number_id}
          </dd>
        </div>
      </dl>

      <InlineAlert
        tone="info"
        title="The access token you entered is not shown here, and cannot be"
        live="off"
      >
        It is sealed when it is saved and read only by the server that calls the WhatsApp Cloud
        API. Nothing sends it back — not masked, not shortened.
      </InlineAlert>

      <InlineAlert tone="info" title="Connecting an account does not approve any templates" live="off">
        Meta reviews each template on its own. Check{" "}
        <Link to="/whatsapp/templates" className="font-semibold underline underline-offset-2">
          Templates
        </Link>{" "}
        for the real approval status before relying on one.
      </InlineAlert>

      <div>
        <Button variant="outline" onClick={onReplace}>
          Replace account
        </Button>
      </div>
    </Card>
  );
}

function PersonalWhatsAppCard() {
  const [testPhone, setTestPhone] = useState("+91 98765 43210");
  const [testMessage, setTestMessage] = useState(
    "Hi, I want to streamline CRM, attendance, and invoicing for our pharma distribution team. Can OpsGrids help?",
  );
  const [simulatedReply, setSimulatedReply] = useState<string | null>(null);
  const [isSimulating, setIsSimulating] = useState(false);

  const runSimulation = () => {
    setIsSimulating(true);
    setTimeout(() => {
      setSimulatedReply(
        "Hi! Thanks for reaching out to OpsGrids. OpsGrids brings CRM, attendance, invoicing, and inventory onto one unified platform with single billing so your team doesn't have to juggle disconnected tools. Would you be open to a quick 10-minute walkthrough this week?",
      );
      setIsSimulating(false);
    }, 300);
  };

  return (
    <Card className="flex flex-col gap-6 p-6 border-positive/30 bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-card bg-positive-soft text-positive">
            <MessageSquareIcon className="size-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold tracking-tight text-ink">Personal WhatsApp (QR Code Multi-Device)</h2>
            <p className="text-body-sm text-muted">
              Link your personal phone directly via QR code. No Meta Business Account, Facebook approval, or message fees required.
            </p>
          </div>
        </div>
        <div className="inline-flex items-center gap-2 rounded-full bg-positive-soft px-3 py-1 text-caption font-semibold text-positive">
          <span className="size-2 rounded-full bg-positive animate-pulse" />
          Bridge Ready
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div className="flex flex-col gap-4">
          <h3 className="text-body-sm font-bold text-ink">How to Link Your Personal WhatsApp</h3>
          <ol className="flex flex-col gap-2 text-body-sm text-ink-soft">
            <li className="flex items-start gap-2">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-positive-soft font-bold text-positive text-caption">1</span>
              <span>Open <strong>WhatsApp</strong> on your personal phone.</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-positive-soft font-bold text-positive text-caption">2</span>
              <span>Go to <strong>Settings</strong> (iOS) or <strong>⋮ Menu</strong> (Android) &rarr; <strong>Linked Devices</strong>.</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-positive-soft font-bold text-positive text-caption">3</span>
              <span>Tap <strong>Link a Device</strong> and scan the terminal QR code.</span>
            </li>
          </ol>

          <div className="rounded-card bg-surface-muted p-4 border border-line">
            <p className="text-caption font-bold uppercase tracking-wider text-muted">Terminal Command to Generate QR</p>
            <code className="mt-1 block font-mono text-body-sm text-ink selection:bg-accent-soft">
              uv run python -m tools.whatsapp_personal.cli connect
            </code>
            <p className="mt-2 text-caption text-muted">
              Renders the Multi-Device QR code and stores your encrypted session in <code>tools/whatsapp_personal/auth_info_baileys/</code>.
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded-card bg-surface-muted p-4 border border-line">
          <div className="flex items-center gap-2 text-ink">
            <SparkIcon className="size-4 text-accent" />
            <h3 className="text-body-sm font-bold">AI Auto-Reply Simulator</h3>
          </div>
          <p className="text-caption text-muted">
            Test how OpsGrids AI (OpenAI ChatGPT / Google Gemini) replies to WhatsApp prospects in a concise, friendly tone.
          </p>

          <TextField
            label="Simulated Customer Phone"
            value={testPhone}
            onChange={(e) => setTestPhone(e.target.value)}
            placeholder="+91 98765 43210"
          />
          <TextArea
            label="Incoming Message"
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            rows={2}
          />
          <div>
            <Button
              size="sm"
              variant="outline"
              onClick={runSimulation}
              disabled={isSimulating || !testMessage.trim()}
              leadingIcon={<SendIcon className="size-3.5" />}
            >
              {isSimulating ? "Generating AI Reply..." : "Simulate Auto-Reply"}
            </Button>
          </div>

          {simulatedReply && (
            <div className="mt-1 rounded-card bg-card p-3 border border-positive/30">
              <p className="text-caption font-semibold text-positive flex items-center gap-2">
                <CheckCircleIcon className="size-3.5" /> AI Response (WhatsApp channel):
              </p>
              <p className="mt-1 text-body-sm text-ink leading-relaxed">
                {simulatedReply}
              </p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function WhatsAppConnection() {
  const loader = useCallback(() => getWhatsAppConnection(), []);
  const state = useAsync(loader, []);
  const [justSaved, setJustSaved] = useState<WhatsAppConnectionOut | null>(null);
  const [replacing, setReplacing] = useState(false);
  const loaded = state.status === "success" ? (state.data.items[0] ?? null) : null;
  const connection = justSaved ?? loaded;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="WhatsApp"
        title="Connection"
        description="Connect WhatsApp to automate messages and AI auto-replies. Link your personal WhatsApp via QR code or configure official Meta Cloud API credentials."
      />

      <PersonalWhatsAppCard />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-5 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-20 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load the WhatsApp connection" />
      ) : null}

      {/* The unconnected state is the form itself — a panel above it saying "not connected" would
          be the same sentence twice, and only one of them acts. */}
      {state.status === "success" && connection === null && !replacing ? (
        <WhatsAppForm mode="connect" onSaved={setJustSaved} />
      ) : null}

      {connection !== null && !replacing ? (
        <ConnectionPanel connection={connection} onReplace={() => setReplacing(true)} />
      ) : null}

      {replacing ? (
        <WhatsAppForm
          mode="replace"
          onSaved={(saved) => {
            setJustSaved(saved);
            setReplacing(false);
          }}
          onCancel={() => setReplacing(false)}
        />
      ) : null}
    </div>
  );
}

/** The screen's single polite live region. */
export function WhatsAppConnectionPage() {
  return (
    <Announcer>
      <WhatsAppConnection />
    </Announcer>
  );
}
