import { useCallback, useMemo, useState } from "react";

import { useAsync, useMutation } from "@opsgrids/client";
import { SendIcon } from "@opsgrids/ui";

import { listWhatsAppTemplates } from "@/api/client";
import type { ProviderTemplateOut } from "@/api/types";
import { sendWhatsAppTestMessage } from "@/api/whatsappSignup";
import type { TestMessageIn } from "@/api/whatsappSignup";
import { failureMessage } from "@/lib/errors";
import { Button, ErrorState, InlineAlert, Select, Skeleton, TextField, useAnnounce } from "@/ui";
import { isE164, toE164 } from "./labels";
import { LinkButton } from "./parts";

function languageName(code: string): string {
  const primary = code.split(/[-_]/)[0] ?? code;
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(primary) ?? code;
  } catch {
    return code;
  }
}

function categoryName(category: string): string {
  return `${category.charAt(0).toUpperCase()}${category.slice(1)}`;
}

function optionLabel(template: ProviderTemplateOut): string {
  return `${template.provider_name} (${categoryName(template.category)}, ${languageName(template.language)})`;
}

/**
 * "Send a test message" — a real message through the real send path, so it follows the same
 * opt-in rules as everything else (the server runs the consent decision and answers 409 when
 * there is no connection; both are shown in the server's own words).
 *
 * Client validation mirrors the server's: a number must be E.164 once spaces and dashes are taken
 * out. It never replaces it — a 422 from the server lands on the field it names. Everything typed
 * stays on failure, and the button is disabled with a visible label while the send is in flight.
 */
export function TestMessageForm() {
  const announce = useAnnounce();
  const loader = useCallback(() => listWhatsAppTemplates(), []);
  const templates = useAsync(loader, []);

  const approved = useMemo(
    () => (templates.status === "success" ? templates.data.items.filter((t) => t.status === "approved") : []),
    [templates],
  );

  const [to, setTo] = useState("");
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [toError, setToError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const send = useMutation((input: TestMessageIn) => sendWhatsAppTestMessage(input), {
    onSuccess: () => {
      setSent(true);
      announce("Test message sent.");
    },
  });

  const chosen = approved.find((t) => t.id === templateId) ?? approved[0] ?? null;

  function submit() {
    setSent(false);
    const number = toE164(to);
    if (!isE164(number)) {
      setToError("Enter the number with its country code, like +91 98765 43210.");
      return;
    }
    setToError(null);
    if (chosen === null) return;
    void send.mutate({ to: number, template_name: chosen.provider_name, language: chosen.language });
  }

  return (
    <section aria-labelledby="test-message-heading" className="flex flex-col gap-4 rounded-card bg-ground p-4">
      <div className="flex flex-col gap-1">
        <h2 id="test-message-heading" className="text-card-title text-ink">
          Send a test message
        </h2>
        <p className="text-body-sm text-muted">
          Use your own number. Test messages follow the same opt-in rules as everything else.
        </p>
      </div>

      {templates.status === "loading" ? (
        <div aria-hidden className="flex flex-col gap-3">
          <Skeleton className="h-11 w-full rounded-tile" />
          <Skeleton className="h-11 w-full rounded-tile" />
        </div>
      ) : null}

      {templates.status === "error" ? (
        <ErrorState
          title="Couldn't load your templates"
          message={templates.error.message}
          onRetry={templates.reload}
        />
      ) : null}

      {templates.status === "success" && approved.length === 0 ? (
        <InlineAlert
          tone="info"
          live="off"
          title="No approved templates yet"
          action={<LinkButton to="/whatsapp/templates">Open Templates</LinkButton>}
        >
          A test message is sent with a template Meta has approved.
        </InlineAlert>
      ) : null}

      {templates.status === "success" && approved.length > 0 ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
          noValidate
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField
              label="Send to"
              type="tel"
              autoComplete="tel"
              inputMode="tel"
              placeholder="+91 98765 43210"
              value={to}
              onChange={(event) => {
                setTo(event.target.value);
                setToError(null);
                setSent(false);
                send.reset();
              }}
              disabled={send.pending}
              required
              error={toError ?? send.fieldErrors.to}
            />
            <Select
              label="Template"
              value={chosen?.id ?? ""}
              onChange={(event) => {
                setTemplateId(event.target.value);
                setSent(false);
                send.reset();
              }}
              disabled={send.pending}
              options={approved.map((t) => ({ value: t.id, label: optionLabel(t) }))}
              error={send.fieldErrors.template_name ?? send.fieldErrors.language}
            />
          </div>

          <div aria-live="polite" className="empty:hidden">
            {send.error !== null && Object.keys(send.fieldErrors).length === 0 ? (
              <InlineAlert tone="danger" title="The test message wasn't sent">
                {failureMessage(send.error)}
              </InlineAlert>
            ) : null}
            {sent ? (
              <InlineAlert tone="success" title="Test message sent">
                It should arrive on WhatsApp in a few seconds.
              </InlineAlert>
            ) : null}
          </div>

          <div>
            <Button
              type="submit"
              loading={send.pending}
              leadingIcon={<SendIcon className="size-4" aria-hidden />}
            >
              {send.pending ? "Sending…" : "Send test message"}
            </Button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
