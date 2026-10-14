import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, Can, useAsync, useMutation } from "@opsgrids/client";
import {
  FileTextIcon,
  MobileDataCard,
  PlusIcon,
  RefreshIcon,
  TABLET_QUERY,
  Tabs,
  Toaster,
  useMediaQuery,
  useToast,
} from "@opsgrids/ui";
import type { TabItem } from "@opsgrids/ui";

import { listTemplates, syncTemplates } from "@/api/whatsappTemplates";
import type { WhatsAppTemplate } from "@/api/whatsappTemplates";
import { failureMessage, isNotActivated } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  SearchInput,
  Select,
  Skeleton,
  TemplateStatusTag,
  useAnnounce,
} from "@/ui";
import type { SelectOption } from "@/ui";
import { EngageNotActive } from "@/ui";

import { tabFor, syncSummary } from "./filters";
import type { CategoryFilter, Filters, StatusFilter } from "./filters";
import { CATEGORY_LABEL, languageLabel, templateName } from "./labels";
import { TemplateDetailBody } from "./TemplateDetailBody";
import { useTemplateView } from "./useTemplateView";

/**
 * The organization's WhatsApp templates, with the approval status Meta gave each one.
 *
 * **Nothing here fabricates a status.** `status` is read verbatim from the row; a template this
 * app has just submitted is `pending` until Meta answers, and the status changes here only when a
 * sync or Meta's own webhook says so. Approval is Meta's decision, and this screen only shows it.
 *
 * Filters are one `{category, status}` pair. The tab row is the quick way to set one of the two
 * (each tab clears the other), and the two selects beside the search box combine them; a
 * combination no tab stands for leaves the row with nothing selected rather than lighting the
 * wrong tab.
 */

const PAGE_SIZE = 20;
const WRITE = "engage.whatsapp.template.write";
const NEW_TEMPLATE = "/whatsapp/templates/new";

const TAB_ITEMS: TabItem[] = [
  { id: "all", label: "All templates" },
  { id: "marketing", label: "Marketing" },
  { id: "utility", label: "Utility" },
  { id: "authentication", label: "Authentication" },
  { id: "draft", label: "Draft" },
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
];

const TAB_FILTERS: Record<string, Filters> = {
  all: { category: "", status: "" },
  marketing: { category: "marketing", status: "" },
  utility: { category: "utility", status: "" },
  authentication: { category: "authentication", status: "" },
  draft: { category: "", status: "draft" },
  pending: { category: "", status: "pending" },
  approved: { category: "", status: "approved" },
  rejected: { category: "", status: "rejected" },
};

const CATEGORY_OPTIONS: SelectOption[] = [
  { value: "", label: "All categories" },
  { value: "marketing", label: "Marketing" },
  { value: "utility", label: "Utility" },
  { value: "authentication", label: "Authentication" },
];

const STATUS_OPTIONS: SelectOption[] = [
  { value: "", label: "All statuses" },
  { value: "draft", label: "Draft" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "paused", label: "Paused" },
  { value: "disabled", label: "Disabled" },
];

const NO_TEMPLATES: WhatsAppTemplate[] = [];
const LOADING_ROWS = [0, 1, 2, 3, 4, 5];

function TemplateRow({
  template,
  selected,
  onPreview,
}: {
  template: WhatsAppTemplate;
  selected: boolean;
  onPreview: () => void;
}) {
  const name = templateName(template);
  return (
    <tr className={selected ? "border-t border-line bg-accent-soft" : "border-t border-line"}>
      <th scope="row" className="w-1/3 min-w-32 max-w-0 py-3 pl-4 pr-4 text-left">
        <Link
          to={`/whatsapp/templates/${encodeURIComponent(template.id)}`}
          className="relative hit-44 block truncate font-mono text-body-sm font-bold text-ink underline-offset-4 hover:text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {name}
        </Link>
      </th>
      <td className="py-3 pr-4 text-body-sm text-ink">{CATEGORY_LABEL[template.category]}</td>
      <td className="hidden py-3 pr-4 text-body-sm text-ink md:table-cell">
        {languageLabel(template.language)}
      </td>
      <td className="py-3 pr-4">
        <TemplateStatusTag status={template.status} />
      </td>
      <td className="hidden py-3 pr-4 text-body-sm text-muted tnum lg:table-cell">
        {formatInstant(template.updated_at)}
      </td>
      <td className="py-3 pr-4 text-right">
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={selected}
          aria-label={`Preview ${name}`}
          onClick={onPreview}
        >
          Preview
        </Button>
      </td>
    </tr>
  );
}

/** One template as a card, for a phone. Tapping it opens the template's own page: a side preview
 *  panel below the list is a second screen's worth of scrolling to read what one tap shows. */
function TemplateCard({ template, onOpen }: { template: WhatsAppTemplate; onOpen: () => void }) {
  return (
    <MobileDataCard
      title={<span className="font-mono">{templateName(template)}</span>}
      status={<TemplateStatusTag status={template.status} />}
      onOpen={onOpen}
      fields={[
        { key: "category", label: "Category", value: CATEGORY_LABEL[template.category] },
        { key: "language", label: "Language", value: languageLabel(template.language) },
        { key: "updated", label: "Updated", value: formatInstant(template.updated_at) },
      ]}
    />
  );
}

function TableSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-4" aria-busy="true">
      <span className="sr-only" role="status">
        Loading templates
      </span>
      {LOADING_ROWS.map((row) => (
        <Skeleton key={row} className="h-10 w-full rounded-tile" />
      ))}
    </div>
  );
}

function PreviewPanel({ id, loading }: { id: string | null; loading: boolean }) {
  const navigate = useNavigate();
  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-section text-ink">Template preview</h2>
      {id === null && loading ? (
        <Skeleton className="h-64 w-full rounded-card" />
      ) : id === null ? (
        <EmptyState
          title="Nothing to preview"
          description="Pick a template from the list to see how it reads, its variables and its approval history."
        />
      ) : (
        <PanelBody id={id} onOpen={() => navigate(`/whatsapp/templates/${encodeURIComponent(id)}`)} />
      )}
    </Card>
  );
}

function PanelBody({ id, onOpen }: { id: string; onOpen: () => void }) {
  const state = useTemplateView(id);
  if (state.status === "loading") {
    return (
      <div aria-busy="true" className="flex flex-col gap-4">
        <span className="sr-only" role="status">
          Loading preview
        </span>
        <Skeleton className="h-8 w-full rounded-tile" />
        <Skeleton className="h-32 w-full rounded-card" />
        <Skeleton className="h-16 w-full rounded-tile" />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <ErrorState
        title="Couldn't load this template"
        message={failureMessage(state.error)}
        onRetry={state.reload}
      />
    );
  }
  const { detail } = state.data;
  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate font-mono text-body font-bold text-ink">
          {templateName(detail)}
        </span>
        <TemplateStatusTag status={detail.status} />
      </div>
      <TemplateDetailBody view={state.data} level="h3" />
      <Button variant="secondary" onClick={onOpen}>
        View details
      </Button>
    </>
  );
}

function TemplatesList() {
  const announce = useAnnounce();
  const toast = useToast();
  const navigate = useNavigate();
  const [filters, setFilters] = useState<Filters>({ category: "", status: "" });
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { category, status } = filters;
  const loader = useCallback(
    () =>
      listTemplates({
        ...(category !== "" ? { category } : {}),
        ...(status !== "" ? { status } : {}),
        q: query,
        page,
        size: PAGE_SIZE,
      }),
    [category, status, query, page],
  );
  const state = useAsync(loader, [category, status, query, page]);

  const sync = useMutation(syncTemplates, {
    onSuccess: (result) => {
      const { title, description } = syncSummary(result);
      toast.success(title, { description });
      state.reload();
    },
    onError: (error) => {
      const reference = error instanceof ApiError ? error.problem?.request_id : undefined;
      toast.error("Couldn't sync from Meta", {
        description: failureMessage(error),
        ...(reference !== undefined ? { reference } : {}),
      });
    },
  });

  const items = state.status === "success" ? state.data.items : NO_TEMPLATES;
  const total = state.status === "success" ? state.data.total : null;
  useEffect(() => {
    if (total !== null) announce(total === 1 ? "1 template" : `${total} templates`);
  }, [total, announce]);

  // The row on show in the side panel: kept while it is still in the list, else the first row, so
  // a filter change never leaves the panel describing a template that is no longer listed.
  useEffect(() => {
    if (state.status !== "success") return;
    setSelectedId((current) =>
      current !== null && items.some((template) => template.id === current)
        ? current
        : (items[0]?.id ?? null),
    );
  }, [state.status, items]);

  const filtered = category !== "" || status !== "" || query.trim() !== "";

  function applyFilters(next: Filters) {
    setFilters(next);
    setPage(1);
  }

  function clearFilters() {
    applyFilters({ category: "", status: "" });
    setQuery("");
  }

  const wide = useMediaQuery(TABLET_QUERY, true);

  const canWrite = (node: ReactNode) => (
    <Can permission={WRITE} scope="ORG">
      {node}
    </Can>
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="WhatsApp"
        title="WhatsApp templates"
        description="Manage and sync your WhatsApp message templates."
        actions={
          <div className="flex flex-wrap items-center gap-3">
            {canWrite(
              <Button
                variant="secondary"
                loading={sync.pending}
                leadingIcon={<RefreshIcon className="size-4" />}
                onClick={() => void sync.mutate(undefined)}
              >
                {sync.pending ? "Syncing…" : "Sync from Meta"}
              </Button>,
            )}
            {canWrite(
              <Button
                leadingIcon={<PlusIcon className="size-4" />}
                onClick={() => navigate(NEW_TEMPLATE)}
              >
                Create template
              </Button>,
            )}
          </div>
        }
      />

      {state.status === "error" && isNotActivated(state.error) ? (
        <EngageNotActive />
      ) : (
        <Tabs
          label="Template filters"
          items={TAB_ITEMS}
          value={tabFor(filters)}
          onChange={(id) => applyFilters(TAB_FILTERS[id] ?? TAB_FILTERS["all"] ?? filters)}
        >
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="flex flex-col lg:col-span-2">
              <div className="flex flex-col gap-3 p-4 sm:flex-row">
                <SearchInput
                  label="Search templates"
                  placeholder="Search templates..."
                  value={query}
                  onDebouncedChange={(value) => {
                    setQuery(value);
                    setPage(1);
                  }}
                  containerClassName="sm:flex-1"
                />
                <Select
                  label="Category"
                  hideLabel
                  value={category}
                  options={CATEGORY_OPTIONS}
                  onChange={(event) =>
                    applyFilters({ ...filters, category: event.target.value as CategoryFilter })
                  }
                  containerClassName="sm:w-48"
                />
                <Select
                  label="Status"
                  hideLabel
                  value={status}
                  options={STATUS_OPTIONS}
                  onChange={(event) =>
                    applyFilters({ ...filters, status: event.target.value as StatusFilter })
                  }
                  containerClassName="sm:w-40"
                />
              </div>

              {state.status === "loading" ? <TableSkeleton /> : null}

              {state.status === "error" ? (
                <ErrorState
                  title="Couldn't load your WhatsApp templates"
                  message={state.error.message}
                  onRetry={state.reload}
                />
              ) : null}

              {state.status === "success" && items.length === 0 ? (
                filtered ? (
                  <EmptyState
                    icon={<FileTextIcon className="size-6" />}
                    title="No templates match"
                    description="Nothing matches the search and filters you have set. Clear them to see every template."
                    action={
                      <Button variant="secondary" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    }
                  />
                ) : (
                  <EmptyState
                    icon={<FileTextIcon className="size-6" />}
                    title="No WhatsApp templates yet"
                    description="Create a template to start Meta's review, or sync the ones already in your Meta account. Nothing can send outside a customer's 24-hour service window until one is approved."
                    action={canWrite(<Button onClick={() => navigate(NEW_TEMPLATE)}>Create template</Button>)}
                  />
                )
              ) : null}

              {items.length > 0 && !wide ? (
                <ul className="flex flex-col gap-3 p-4 pt-0" aria-label="WhatsApp templates">
                  {items.map((template) => (
                    <li key={template.id}>
                      <TemplateCard
                        template={template}
                        onOpen={() => navigate(`/whatsapp/templates/${encodeURIComponent(template.id)}`)}
                      />
                    </li>
                  ))}
                </ul>
              ) : null}

              {items.length > 0 && wide ? (
                <div className="relative overflow-x-auto">
                  <table className="w-full border-collapse text-left">
                    <caption className="sr-only">
                      WhatsApp templates, their category, language, Meta's approval status and when
                      they last changed
                    </caption>
                    <thead>
                      <tr className="bg-sunken text-caption font-bold text-muted">
                        <th scope="col" className="w-1/3 min-w-32 py-3 pl-4 pr-4">
                          Name
                        </th>
                        <th scope="col" className="py-3 pr-4">
                          Category
                        </th>
                        <th scope="col" className="hidden py-3 pr-4 md:table-cell">
                          Language
                        </th>
                        <th scope="col" className="py-3 pr-4">
                          Status
                        </th>
                        <th scope="col" className="hidden py-3 pr-4 lg:table-cell">
                          Updated
                        </th>
                        <th scope="col" className="py-3 pr-4">
                          <span className="sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((template) => (
                        <TemplateRow
                          key={template.id}
                          template={template}
                          selected={template.id === selectedId}
                          onPreview={() => setSelectedId(template.id)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {state.status === "success" && items.length > 0 ? (
                <div className="border-t border-line px-4 pb-4">
                  <Pagination
                    page={state.data.page}
                    pages={state.data.pages}
                    total={state.data.total}
                    size={state.data.size}
                    onPageChange={setPage}
                    noun="templates"
                  />
                </div>
              ) : null}
            </Card>

            {wide ? <PreviewPanel id={selectedId} loading={state.status === "loading"} /> : null}
          </div>
        </Tabs>
      )}
    </div>
  );
}

/** The screen's polite live region, and the toasts a sync raises. */
export function TemplatesPage() {
  return (
    <Announcer>
      <Toaster>
        <TemplatesList />
      </Toaster>
    </Announcer>
  );
}
