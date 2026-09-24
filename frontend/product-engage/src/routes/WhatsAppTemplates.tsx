import { useCallback, useState } from "react";

import { Can, useAsync } from "@opsgrids/client";
import { FileTextIcon, PlusIcon } from "@opsgrids/ui";

import { listWhatsAppTemplates, submitWhatsAppTemplate } from "@/api/client";
import type { ProviderTemplateIn, ProviderTemplateOut, TemplateCategory } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  Dialog,
  EmptyState,
  InlineAlert,
  LoadFailure,
  PageHeader,
  Select,
  Skeleton,
  TemplateStatusTag,
  TextField,
  useAnnounce,
} from "@/ui";
import type { SelectOption } from "@/ui";

/**
 * The organization's WhatsApp message templates and the real approval status Meta has given
 * each one — not this app's guess at one.
 *
 * **Nothing here fabricates a status.** `status` and the `sendable` derived from it are read
 * verbatim from `ProviderTemplateOut`; submitting a template only ever produces `draft` (the
 * service's own default — see `submitWhatsAppTemplate`) and this screen never claims otherwise.
 * A template outside a customer's 24-hour service window can only be sent once Meta has actually
 * approved it, and `sendable` is the one fact this screen trusts to say whether that is true.
 *
 * Gated on Engage's own `engage.whatsapp.template.read`/`.write`, held by every Engage seat and
 * `ENGAGE_ADMIN` respectively (`app/catalogue.py`) — not `notification`'s permission of the same
 * shape, which no Engage role ever granted. Submitting a template is wrapped in `Can` on the
 * write permission for the same reason every other Engage write control is: the button hides
 * for a caller who cannot use it, and the service refuses the call regardless.
 */

const CATEGORY_OPTIONS: SelectOption[] = [
  { value: "utility", label: "Utility — order and account updates" },
  { value: "marketing", label: "Marketing — promotions and offers" },
  { value: "authentication", label: "Authentication — one-time codes" },
];

interface Values {
  code: string;
  language: string;
  category: TemplateCategory;
  previewText: string;
}

type Errors = Partial<Record<"code" | "previewText", string>>;

const EMPTY: Values = { code: "", language: "en", category: "utility", previewText: "" };

function validate(values: Values): Errors {
  const errors: Errors = {};
  if (!/^[a-z0-9_]+$/.test(values.code.trim())) {
    errors.code = "Lowercase letters, digits and underscores only, like delivery_confirmed.";
  }
  if (values.previewText.trim() === "") {
    errors.previewText = "What this template says, with {{1}}, {{2}}… for each variable.";
  }
  return errors;
}

function toInput(values: Values): ProviderTemplateIn {
  return {
    code: values.code.trim(),
    channel: "whatsapp",
    provider_name: "meta",
    language: values.language.trim() === "" ? "en" : values.language.trim(),
    category: values.category,
    preview_text: values.previewText.trim(),
  };
}

function NewTemplateDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (template: ProviderTemplateOut) => void;
}) {
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  function close() {
    setErrors({});
    setFailure(null);
    onClose();
  }

  function clearError(key: "code" | "previewText") {
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    if (key === "code" || key === "previewText") clearError(key);
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
      const template = await submitWhatsAppTemplate(toInput(values));
      setValues(EMPTY);
      onSaved(template);
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Submit a WhatsApp template"
      description="Records what this template says. Meta still has to review and approve it before it can send outside a customer's 24-hour service window — this only stages the submission."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Submitting…" : "Submit for review"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That template wasn't submitted" className="mb-4">
            {failure}
          </InlineAlert>
        ) : null}
        {failure === null && Object.keys(errors).length > 0 ? (
          <InlineAlert tone="warning" title="Check the fields marked below" className="mb-4">
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
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Template code"
            value={values.code}
            onChange={(event) => set("code", event.target.value)}
            placeholder="delivery_confirmed"
            autoComplete="off"
            spellCheck={false}
            helper="Your own identifier for this template. Used by automation rules, not shown to customers."
            disabled={submitting}
            {...(errors.code !== undefined ? { error: errors.code } : {})}
          />
          <TextField
            label="Language"
            value={values.language}
            onChange={(event) => set("language", event.target.value)}
            placeholder="en"
            autoComplete="off"
            spellCheck={false}
            helper="A BCP-47 code, like en or en_US."
            disabled={submitting}
          />
        </div>

        <Select
          label="Category"
          value={values.category}
          onChange={(event) => set("category", event.target.value as TemplateCategory)}
          options={CATEGORY_OPTIONS}
          helper="Meta reviews utility, marketing and authentication templates under different rules."
          disabled={submitting}
        />

        <TextField
          label="Preview text"
          value={values.previewText}
          onChange={(event) => set("previewText", event.target.value)}
          placeholder="Hi {{1}}, your shipment is out for delivery today."
          helper="What the template says. Mark each variable as {{1}}, {{2}}, and so on."
          disabled={submitting}
          {...(errors.previewText !== undefined ? { error: errors.previewText } : {})}
        />

        <InlineAlert tone="info" live="off" title="This does not approve the template">
          It records what you intend to send. Meta's own review decides whether it can — check
          back here for the real status once it has answered.
        </InlineAlert>
      </form>
    </Dialog>
  );
}

function TemplateRow({ template }: { template: ProviderTemplateOut }) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <span className="block text-sm font-bold text-ink">{template.id}</span>
        <span className="block text-xs text-muted">{template.language}</span>
      </th>
      <td className="py-3 pr-4 text-sm text-ink capitalize">{template.category}</td>
      <td className="py-3 pr-4 max-w-xs">
        <p className="text-sm text-muted">{template.preview_text}</p>
      </td>
      <td className="py-3 pr-4">
        <TemplateStatusTag status={template.status} />
        {template.status_reason !== null ? (
          <span className="mt-1 block max-w-56 text-xs text-muted">{template.status_reason}</span>
        ) : null}
      </td>
      <td className="py-3 pr-4 text-sm text-muted">
        {template.status_checked_at === null
          ? "Not checked yet"
          : formatInstant(template.status_checked_at)}
      </td>
      <td className="py-3 text-sm">
        {template.sendable ? (
          <span className="font-semibold text-positive">Can send</span>
        ) : (
          <span className="text-muted">Cannot send yet</span>
        )}
      </td>
    </tr>
  );
}

const NO_TEMPLATES: ProviderTemplateOut[] = [];

function WhatsAppTemplates() {
  const announce = useAnnounce();
  const [attempt, setAttempt] = useState(0);
  const [adding, setAdding] = useState(false);

  const loader = useCallback(() => listWhatsAppTemplates(), []);
  const state = useAsync(loader, [attempt]);
  const templates = state.status === "success" ? state.data.items : NO_TEMPLATES;

  function reload(message: string) {
    announce(message);
    setAdding(false);
    setAttempt((current) => current + 1);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="WhatsApp"
        title="Templates"
        description="The status of every WhatsApp template, exactly as Meta reports it. Approval is Meta's decision — this screen only ever shows what it has actually said."
        actions={
          state.status === "success" && templates.length > 0 ? (
            <Can permission="engage.whatsapp.template.write" scope="ORG">
              <Button onClick={() => setAdding(true)} leadingIcon={<PlusIcon className="size-4" />}>
                Submit a template
              </Button>
            </Can>
          ) : null
        }
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your WhatsApp templates" />
      ) : null}

      {state.status === "success" && templates.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<FileTextIcon className="size-6" />}
            title="No WhatsApp templates yet"
            description="Submit a template to start Meta's review. Nothing can send outside a customer's 24-hour service window until one is approved."
            action={
              <Can permission="engage.whatsapp.template.write" scope="ORG">
                <Button onClick={() => setAdding(true)}>Submit a template</Button>
              </Can>
            }
          />
        </Card>
      ) : null}

      {templates.length > 0 ? (
        <Card className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[64rem] border-collapse text-left">
              <caption className="sr-only">
                WhatsApp templates, their category, Meta's approval status and whether each can
                send
              </caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">
                    Template
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Category
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Preview
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Status
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Last checked
                  </th>
                  <th scope="col" className="pb-2">
                    Sendable
                  </th>
                </tr>
              </thead>
              <tbody>
                {templates.map((template) => (
                  <TemplateRow key={template.id} template={template} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <NewTemplateDialog
        open={adding}
        onClose={() => setAdding(false)}
        onSaved={(template) => reload(`${template.id} submitted. Status: ${template.status}.`)}
      />
    </div>
  );
}

/** The screen's single polite live region. */
export function WhatsAppTemplatesPage() {
  return (
    <Announcer>
      <WhatsAppTemplates />
    </Announcer>
  );
}
