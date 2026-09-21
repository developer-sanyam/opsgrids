import { useState } from "react";

import { ArrowLeftIcon, ArrowRightIcon } from "@opsgrids/ui";

import { connectProvider, replaceProvider } from "@/api/client";
import type { ConnectionIn, ConnectionKind, ConnectionOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { connectionKindLabel } from "@/lib/format";
import {
  Button,
  Card,
  Dialog,
  InlineAlert,
  RadioGroup,
  SectionHeader,
  TextField,
  useAnnounce,
} from "@/ui";
import type { RadioOption } from "@/ui";

/**
 * Connecting a provider, as two questions rather than a settings form — and, in `"replace"` mode,
 * the same two questions with a third: what replacing it actually does.
 *
 * **The first question is how the organization sends, not where.** `domain.transport_for` derives
 * the submission host, the port and the daily cap from the kind, so asking for a host up front
 * would be asking the customer to look up something the service already knows — and to get it
 * wrong in a way that only shows up at the first send. Custom SMTP is the one kind whose endpoint
 * cannot be derived, so it is the one kind that reveals a host field.
 *
 * `platform` is the fourth kind in the wire enum and is not offered here. The enum carries it so
 * an API caller gets a sentence about the missing MTA instead of an unexplained enum error;
 * `services.create_connection` (and `replace_connection`) refuses it either way, and a radio whose
 * every outcome is a refusal is a dead end with a label on it.
 *
 * **Nothing in this component renders the password back.** It is held in state until the request
 * carrying it resolves, cleared the moment it does, and has no counterpart on `ConnectionOut` to
 * read back — not masked, not a length, not a last-four.
 *
 * **`mode="replace"` reuses every question above rather than duplicating them.** The one thing it
 * adds is a step `create_connection` has no equivalent of: a confirmation, in the Dialog primitive
 * (ADR-0001 — this app forks no component), that names the actual consequence —
 * `replace_connection` resets every sender in the organization to unusable in the same
 * transaction — before the request that triggers it ever leaves the browser.
 */

/** The three kinds `services.create_connection` and `replace_connection` accept. Descriptions say
 *  what the customer has to have ready, because "Google Workspace" alone does not tell them to
 *  mint an app password. */
const KIND_OPTIONS: RadioOption[] = [
  {
    value: "google",
    label: "Google Workspace",
    description:
      "Mail leaves through Gmail's submission service, signed in as a Workspace account with an app password.",
  },
  {
    value: "microsoft",
    label: "Microsoft 365",
    description:
      "Mail leaves through Exchange Online, signed in as a Microsoft 365 mailbox with an app password.",
  },
  {
    value: "smtp",
    label: "Custom SMTP",
    description:
      "Any other provider — a relay of your own, Postmark, SES. The only choice that needs a submission host, because it is the only one we cannot derive.",
  },
];

/** What the account field is called, per kind. A Workspace admin and a relay operator are looking
 *  for different words, and "Username" serves neither of them well. */
const USERNAME_LABEL: Record<Exclude<ConnectionKind, "platform">, string> = {
  google: "Workspace account",
  microsoft: "Microsoft 365 mailbox",
  smtp: "SMTP username",
};

const SECRET_LABEL: Record<Exclude<ConnectionKind, "platform">, string> = {
  google: "App password",
  microsoft: "App password",
  smtp: "SMTP password",
};

interface Values {
  username: string;
  secret: string;
  host: string;
  /** A string, like every other control's value. Parsed once, at the boundary. */
  port: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const EMPTY: Values = { username: "", secret: "", host: "", port: "" };

/**
 * The rules this form can check without asking the server: a field is missing, or a port is not a
 * port. Everything else — whether the credential works, whether a connection already exists — is
 * the service's to answer, and is shown in its own words when it does.
 */
function validate(kind: SelectableKind, values: Values): Errors {
  const errors: Errors = {};
  if (values.username.trim() === "") {
    errors.username = "Which account does Engage sign in as?";
  }
  if (values.secret === "") {
    errors.secret = "Paste the app password you generated for Engage.";  // pragma: allowlist secret
  }
  if (kind === "smtp" && values.host.trim() === "") {
    // The service's own sentence for this case, so the two agree if it ever gets there.
    errors.host = "An SMTP connection needs the host its provider submits on.";
  }
  const port = values.port.trim();
  if (port !== "" && !/^\d+$/.test(port)) {
    errors.port = "A port is a whole number, like 587.";
  } else if (port !== "" && (Number(port) < 1 || Number(port) > 65535)) {
    errors.port = "A port is between 1 and 65535.";
  }
  return errors;
}

function toInput(kind: SelectableKind, values: Values): ConnectionIn {
  const port = values.port.trim();
  return {
    kind,
    username: values.username.trim(),
    // Not trimmed. A password whose first character is a space is a password with a space in it,
    // and silently editing it produces a credential that fails for a reason nobody can see.
    secret: values.secret,
    // Sent only where it was asked for. For a named provider an empty host means "derive it",
    // and posting one we invented would override the service's own value with a guess.
    ...(kind === "smtp" ? { host: values.host.trim() } : {}),
    ...(port === "" ? {} : { port: Number(port) }),
  };
}

type SelectableKind = Exclude<ConnectionKind, "platform">;

function isSelectable(value: string): value is SelectableKind {
  return value === "google" || value === "microsoft" || value === "smtp";
}

type Mode = "connect" | "replace";
type Step = "kind" | "credentials" | "confirm";

/** The sentence this whole slice exists to make sure nobody misses. Said once, as the Dialog's
 *  own description — tied to the dialog by `aria-describedby` — and again through the live region
 *  when the step is reached, because a step change inside an already-open dialog moves no focus
 *  for a screen reader to follow onto it. */
const REPLACE_CONSEQUENCE =
  "Replacing the connection resets every sender in this organization to unusable. Each one " +
  "needs a new test message before it can send again, and none of them can send until it has.";

export function ConnectionWizard({
  mode = "connect",
  open = true,
  onConnected,
  onCancel,
}: {
  /** `"connect"` is the page's own content while no connection exists yet, wired to `POST` and
   *  rendered as a `Card`. `"replace"` is the identical two questions wired to `PUT`, with a
   *  confirmation step added, rendered as a `Dialog` over the connection the customer already
   *  has. */
  mode?: Mode;
  /** Only meaningful in `"replace"` mode: whether the dialog is open. */
  open?: boolean;
  onConnected: (c: ConnectionOut) => void;
  /** Only meaningful in `"replace"` mode: the dialog was dismissed without replacing anything. */
  onCancel?: () => void;
}) {
  const announce = useAnnounce();
  const [kind, setKind] = useState<SelectableKind>("google");
  const [step, setStep] = useState<Step>("kind");
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    // Clear that field's error as it is corrected, and nothing else: an error that outlives the
    // fix reads as the form not listening.
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function backToKind() {
    setStep("kind");
    // Both messages belong to the fields on the step being left. A "check the fields marked
    // below" over a question with no fields under it marks nothing.
    setFailure(null);
    setErrors({});
  }

  /** Replace mode only: the credentials are valid, so hold them and show what replacing them
   *  actually does before anything is sent. */
  function proceedToConfirm() {
    if (submitting) return;
    const found = validate(kind, values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }
    setFailure(null);
    setStep("confirm");
    announce(`Confirm the replacement. ${REPLACE_CONSEQUENCE}`);
  }

  /** Replace mode only: back to the fields, credentials intact — reconsidering is not cancelling. */
  function backToCredentials() {
    setStep("credentials");
    setFailure(null);
  }

  /** Replace mode only, and only while the dialog is closed via Escape, the backdrop's ✕ or
   *  Cancel. The typed fields survive a close, the same as every other form-carrying dialog in
   *  this app — reopening to five retyped fields costs the customer real work for no reason. What
   *  does not survive is which step they were on: reopening straight onto "Replace provider" from
   *  a dialog the customer just backed out of would read as pre-loaded to commit. */
  function close() {
    setStep("kind");
    setErrors({});
    setFailure(null);
    onCancel?.();
  }

  async function submit() {
    if (submitting) return;
    if (mode === "connect") {
      const found = validate(kind, values);
      setErrors(found);
      if (Object.keys(found).length > 0) {
        setFailure(null);
        return;
      }
    }
    // In "replace" mode, `proceedToConfirm` above is the only path onto the step this is called
    // from, so the fields are already known good.

    setSubmitting(true);
    setFailure(null);
    try {
      const connection =
        mode === "replace"
          ? await replaceProvider(toInput(kind, values))
          : await connectProvider(toInput(kind, values));
      // The credential has left with the request and this is the last moment anything holds it.
      // The component is about to unmount anyway; dropping it does not depend on that happening.
      setValues((current) => ({ ...current, secret: "" }));
      announce(
        mode === "replace"
          ? `${connectionKindLabel(connection.kind)} replaced the previous connection. Every sender needs a new test message.`
          : `${connectionKindLabel(connection.kind)} is connected.`,
      );
      onConnected(connection);
    } catch (error) {
      // Everything typed stays where it is, the password included: it is still in the field the
      // customer typed it into, which is the only place on screen it has ever been. Clearing it
      // here would mean retyping a forty-character app password to retry a save that failed
      // because a connection already existed (connect) or no longer does (replace).
      setFailure(failureMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  const summary = (
    <div aria-live="polite" className="empty:hidden">
      {failure !== null ? (
        <InlineAlert
          tone="danger"
          title={mode === "replace" ? "The connection wasn't replaced" : "That provider wasn't connected"}
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
  );

  const kindStep = (
    <>
      <RadioGroup
        legend="How does your organization send email?"
        name="connection-kind"
        value={kind}
        onChange={(value) => {
          if (isSelectable(value)) setKind(value);
        }}
        options={KIND_OPTIONS}
        helper="The submission host, the port and the daily limit come from this answer. Only a custom server has to be described."
      />
      {mode === "connect" ? (
        <div>
          <Button
            onClick={() => setStep("credentials")}
            trailingIcon={<ArrowRightIcon className="size-4" />}
          >
            Continue
          </Button>
        </div>
      ) : null}
    </>
  );

  const credentialsStep = (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (mode === "replace") {
          proceedToConfirm();
        } else {
          void submit();
        }
      }}
    >
      <div className="flex flex-wrap items-center gap-3 rounded-tile bg-ground px-4 py-3">
        <span className="text-sm text-muted">Sending through</span>
        <span className="text-sm font-bold text-ink">{connectionKindLabel(kind)}</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={backToKind}
          disabled={submitting}
          leadingIcon={<ArrowLeftIcon className="size-4" />}
          className="ml-auto"
        >
          Change
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label={USERNAME_LABEL[kind]}
          value={values.username}
          onChange={(event) => set("username", event.target.value)}
          autoComplete="username"
          placeholder="notifications@your-company.com"
          helper="Mail goes out signed in as this account."
          disabled={submitting}
          {...(errors.username !== undefined ? { error: errors.username } : {})}
        />
        <TextField
          label={SECRET_LABEL[kind]}
          type="password"
          value={values.secret}
          onChange={(event) => set("secret", event.target.value)}
          /* `new-password`, not `current-password`: this is a credential minted for Engage,
             and offering to fill somebody's own mailbox password here invites exactly the
             wrong one. */
          autoComplete="new-password"
          disabled={submitting}
          {...(errors.secret !== undefined ? { error: errors.secret } : {})}
        />
      </div>

      {/* The one kind whose endpoint the service cannot derive. Revealed here rather than
          asked up front, so the other two customers never see a field they would have to
          research. */}
      {kind === "smtp" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Submission host"
            value={values.host}
            onChange={(event) => set("host", event.target.value)}
            placeholder="smtp.your-provider.com"
            helper="The host your provider accepts submissions on."
            disabled={submitting}
            {...(errors.host !== undefined ? { error: errors.host } : {})}
          />
          <TextField
            label="Port"
            inputMode="numeric"
            value={values.port}
            onChange={(event) => set("port", event.target.value)}
            placeholder="587"
            optional
            helper="Leave it empty for 587 with STARTTLS, which is what the service uses."
            disabled={submitting}
            {...(errors.port !== undefined ? { error: errors.port } : {})}
          />
        </div>
      ) : null}

      <InlineAlert tone="info" live="off" title="This password is never shown again">
        It is sealed when it is saved and read only by the server that opens the connection.
        Nothing sends it back to this screen — not masked, not shortened, not counted.
      </InlineAlert>

      {mode === "connect" ? (
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={submitting}>
            {submitting ? "Connecting…" : "Connect provider"}
          </Button>
        </div>
      ) : null}
    </form>
  );

  const confirmStep = (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
        <span className="text-sm text-muted">Replacing with</span>
        <span className="text-sm font-bold text-ink">
          {connectionKindLabel(kind)} — {values.username.trim()}
        </span>
      </div>
      <InlineAlert tone="warning" live="off" title="Every sender needs a new test message after this">
        {REPLACE_CONSEQUENCE}
      </InlineAlert>
    </div>
  );

  if (mode === "connect") {
    return (
      <Card className="flex flex-col gap-5 p-6">
        <SectionHeader
          title="Connect your provider"
          subtitle="Engage sends through your own mail provider. No domain, no sender and no campaign can do anything until one is connected."
        />
        {summary}
        {step === "kind" ? kindStep : credentialsStep}
      </Card>
    );
  }

  const footer =
    step === "kind" ? (
      <>
        <Button variant="ghost" onClick={close}>
          Cancel
        </Button>
        <Button onClick={() => setStep("credentials")} trailingIcon={<ArrowRightIcon className="size-4" />}>
          Continue
        </Button>
      </>
    ) : step === "credentials" ? (
      <>
        <Button variant="ghost" onClick={close} disabled={submitting}>
          Cancel
        </Button>
        <Button onClick={proceedToConfirm} disabled={submitting}>
          Continue
        </Button>
      </>
    ) : (
      <>
        <Button variant="ghost" onClick={backToCredentials} disabled={submitting}>
          Back
        </Button>
        <Button variant="danger" onClick={() => void submit()} disabled={submitting}>
          {submitting ? "Replacing…" : "Replace provider"}
        </Button>
      </>
    );

  const title =
    step === "confirm" ? "Confirm the replacement" : "Replace your provider";
  const description =
    step === "confirm"
      ? REPLACE_CONSEQUENCE
      : "Collects the same details as connecting for the first time. Nothing is sent until you confirm the last screen.";

  return (
    <Dialog
      open={open}
      onClose={close}
      title={title}
      description={description}
      footer={footer}
      size="lg"
    >
      <div className="flex flex-col gap-5">
        {summary}
        {step === "kind" ? kindStep : step === "credentials" ? credentialsStep : confirmStep}
      </div>
    </Dialog>
  );
}
