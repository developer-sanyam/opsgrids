import { useState } from "react";

import { ArrowLeftIcon, ArrowRightIcon } from "@opsgrids/ui";

import { connectProvider } from "@/api/client";
import type { ConnectionIn, ConnectionKind, ConnectionOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { connectionKindLabel } from "@/lib/format";
import {
  Button,
  Card,
  InlineAlert,
  RadioGroup,
  SectionHeader,
  TextField,
  useAnnounce,
} from "@/ui";
import type { RadioOption } from "@/ui";

/**
 * Connecting a provider, as two questions rather than a settings form.
 *
 * **The first question is how the organization sends, not where.** `domain.transport_for` derives
 * the submission host, the port and the daily cap from the kind, so asking for a host up front
 * would be asking the customer to look up something the service already knows — and to get it
 * wrong in a way that only shows up at the first send. Custom SMTP is the one kind whose endpoint
 * cannot be derived, so it is the one kind that reveals a host field.
 *
 * `platform` is the fourth kind in the wire enum and is not offered here. The enum carries it so
 * an API caller gets a sentence about the missing MTA instead of an unexplained enum error;
 * `services.create_connection` refuses it either way, and a radio whose every outcome is a
 * refusal is a dead end with a label on it.
 *
 * **Nothing in this component renders the password back.** It is held in state until the request
 * carrying it resolves, cleared the moment it does, and has no counterpart on `ConnectionOut` to
 * read back — not masked, not a length, not a last-four.
 */

/** The three kinds `services.create_connection` accepts. Descriptions say what the customer has
 *  to have ready, because "Google Workspace" alone does not tell them to mint an app password. */
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
    errors.secret = "Paste the app password you generated for Engage.";
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

export function ConnectionWizard({ onConnected }: { onConnected: (c: ConnectionOut) => void }) {
  const announce = useAnnounce();
  const [kind, setKind] = useState<SelectableKind>("google");
  const [step, setStep] = useState<"kind" | "credentials">("kind");
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

  async function submit() {
    if (submitting) return;
    const found = validate(kind, values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      const connection = await connectProvider(toInput(kind, values));
      // The credential has left with the request and this is the last moment anything holds it.
      // The component is about to unmount anyway; dropping it does not depend on that happening.
      setValues((current) => ({ ...current, secret: "" }));
      announce(`${connectionKindLabel(connection.kind)} is connected.`);
      onConnected(connection);
    } catch (error) {
      // Everything typed stays where it is, the password included: it is still in the field the
      // customer typed it into, which is the only place on screen it has ever been. Clearing it
      // here would mean retyping a forty-character app password to retry a save that failed
      // because a connection already existed.
      setFailure(failureMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="flex flex-col gap-5 p-6">
      <SectionHeader
        title="Connect your provider"
        subtitle="Engage sends through your own mail provider. No domain, no sender and no campaign can do anything until one is connected."
      />

      {/* One summary, announced once. The fields carry their own messages by `aria-describedby`,
          so a five-error submit does not interrupt five times. */}
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That provider wasn't connected">
            {failure}
          </InlineAlert>
        ) : null}
        {failure === null && Object.keys(errors).length > 0 ? (
          <InlineAlert tone="warning" title="Check the fields marked below">
            Nothing has been sent yet.
          </InlineAlert>
        ) : null}
      </div>

      {step === "kind" ? (
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
          <div>
            <Button onClick={() => setStep("credentials")} trailingIcon={<ArrowRightIcon className="size-4" />}>
              Continue
            </Button>
          </div>
        </>
      ) : (
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="flex flex-wrap items-center gap-3 rounded-tile bg-ground px-4 py-3">
            <span className="text-sm text-muted">Sending through</span>
            <span className="text-sm font-bold text-ink">{connectionKindLabel(kind)}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setStep("kind");
                // Both messages belong to the fields on the step being left. A "check the fields
                // marked below" over a question with no fields under it marks nothing.
                setFailure(null);
                setErrors({});
              }}
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

          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={submitting}>
              {submitting ? "Connecting…" : "Connect provider"}
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}
