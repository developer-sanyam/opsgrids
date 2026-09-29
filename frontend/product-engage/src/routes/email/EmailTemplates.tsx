import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Can, useAsync } from "@opsgrids/client";
import { MailIcon } from "@opsgrids/ui";

import { listEmailTemplates } from "@/api/email";
import type { EmailTemplateOut } from "@/api/email";
import { formatInstant, formatNumber } from "@/lib/format";
import { Button, Card, EmptyState, PageHeading, SearchInput, Skeleton } from "@/ui";

import { TemplatesFailure } from "./TemplatesFailure";

/**
 * Every email template in the organization.
 *
 * The columns are what the service sends: the code, the subject, how many variables the template
 * names, and when it last changed. The design also shows a category, a status, a language and where
 * a template is used; `TemplateOut` carries none of them, so there are no category tabs and no
 * status badges here — a filter over a value the service never returns would filter nothing.
 *
 * Search runs over the loaded page. The service has no search parameter, and one full page is the
 * working set every Engage list takes.
 */

const CREATE_PERMISSION = "engage.email.template.write";

function matches(template: EmailTemplateOut, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  return [template.id, template.subject ?? "", template.description].some((text) =>
    text.toLowerCase().includes(needle),
  );
}

function CreateButton() {
  const navigate = useNavigate();
  return (
    <Can permission={CREATE_PERMISSION} scope="ORG">
      <Button onClick={() => navigate("/email/templates/new")}>Create Template</Button>
    </Can>
  );
}

function TemplateRow({ template }: { template: EmailTemplateOut }) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <Link
          to={`/email/templates/${encodeURIComponent(template.id)}`}
          className="text-sm font-bold text-ink underline-offset-4 hover:text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {template.id}
        </Link>
        {template.description !== "" ? (
          <span className="mt-1 block text-xs text-muted">{template.description}</span>
        ) : null}
      </th>
      <td className="py-3 pr-4 text-sm text-ink">{template.subject ?? "—"}</td>
      <td className="py-3 pr-4 text-sm tabular-nums text-ink">
        {formatNumber(template.placeholders.length)}
      </td>
      <td className="py-3 pr-4 text-sm text-muted">{formatInstant(template.updated_at)}</td>
    </tr>
  );
}

export function EmailTemplatesPage() {
  const state = useAsync(listEmailTemplates, []);
  const [query, setQuery] = useState("");

  const templates = state.status === "success" ? state.data.items : null;
  const shown = useMemo(
    () => (templates === null ? [] : templates.filter((t) => matches(t, query))),
    [templates, query],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeading
        eyebrow="Email"
        title="Email Templates"
        description="Create and manage email templates for campaigns and automations."
        actions={<CreateButton />}
      />

      <div aria-live="polite" className="sr-only">
        {state.status === "loading" ? "Loading email templates" : ""}
      </div>

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-11 w-full sm:max-w-sm" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <TemplatesFailure error={state.error} onRetry={state.reload} />
      ) : null}

      {state.status === "success" && templates !== null && templates.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<MailIcon className="size-6" />}
            title="No email templates yet"
            description="A template is a subject and body with placeholders, written once and filled in for each message. Create one to reuse it."
            action={<CreateButton />}
          />
        </Card>
      ) : null}

      {state.status === "success" && templates !== null && templates.length > 0 ? (
        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <SearchInput
            label="Search templates"
            placeholder="Search templates..."
            containerClassName="sm:max-w-sm"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />

          {shown.length === 0 ? (
            <EmptyState
              icon={<MailIcon className="size-6" />}
              title="No templates match that search"
              description="Try part of a template name or subject, or clear the search."
              action={
                <Button variant="secondary" onClick={() => setQuery("")}>
                  Clear search
                </Button>
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-2xl border-collapse text-left">
                <caption className="sr-only">
                  Email templates, their subject, how many variables each names, and when each last changed
                </caption>
                <thead>
                  <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                    <th scope="col" className="pb-2 pr-4">
                      Name
                    </th>
                    <th scope="col" className="pb-2 pr-4">
                      Subject
                    </th>
                    <th scope="col" className="pb-2 pr-4">
                      Variables
                    </th>
                    <th scope="col" className="pb-2 pr-4">
                      Updated
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((template) => (
                    <TemplateRow key={template.id} template={template} />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="text-xs text-muted">
            Showing {formatNumber(shown.length)} of {formatNumber(state.data.total)} templates
          </p>
        </Card>
      ) : null}
    </div>
  );
}
