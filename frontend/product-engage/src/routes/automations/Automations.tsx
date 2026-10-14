import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { Menu, MoreHorizontalIcon, SparkIcon, Tabs } from "@opsgrids/ui";
import type { MenuItem, TabItem } from "@opsgrids/ui";

import {
  deleteAutomation,
  listAutomations,
  listTriggers,
  pauseAutomation,
  resumeAutomation,
} from "@/api/automations";
import type { AutomationOut, AutomationStatus, TriggerOut } from "@/api/automations";
import { failureMessage } from "@/lib/errors";
import { formatNumber } from "@/lib/format";
import { useRouteNotice } from "@/lib/useRouteNotice";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  PageHeading,
  Pagination,
  SearchInput,
  Select,
  Skeleton,
  useAnnounce,
  useDebouncedValue,
} from "@/ui";
import type { SelectOption } from "@/ui";
import { formatDay, formatRate } from "./format";
import { AutomationStatusTag } from "./Tags";
import { LoadError } from "./LoadError";
import { RECIPES } from "./recipes";

/**
 * Every automation this organization has built, whichever channel it sends on: a draft being
 * shaped, an active one running, a paused one on hold. Pausing, resuming and deleting each ask
 * first, because each changes what customers receive (or removes the flow), and each says what the
 * service answered rather than assuming it worked.
 */

type TabId = "all" | AutomationStatus;

const TABS: TabItem[] = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "paused", label: "Paused" },
  { id: "draft", label: "Draft" },
];

const NO_ROWS: AutomationOut[] = [];
const NO_TRIGGERS: TriggerOut[] = [];

type Action = "pause" | "resume" | "delete";

interface PendingAction {
  action: Action;
  automation: AutomationOut;
}

const CONFIRM: Record<
  Action,
  {
    title: string;
    confirm: string;
    pending: string;
    tone: "primary" | "danger";
    body: (name: string) => string;
    done: (name: string) => string;
  }
> = {
  pause: {
    title: "Pause this automation?",
    confirm: "Pause automation",
    pending: "Pausing…",
    tone: "primary",
    body: (name) =>
      `“${name}” stops starting new runs. Runs already in progress keep going. You can resume it at any time.`,
    done: (name) => `${name} paused.`,
  },
  resume: {
    title: "Resume this automation?",
    confirm: "Resume automation",
    pending: "Resuming…",
    tone: "primary",
    body: (name) => `“${name}” starts running again for new events.`,
    done: (name) => `${name} resumed.`,
  },
  delete: {
    title: "Delete this automation?",
    confirm: "Delete automation",
    pending: "Deleting…",
    tone: "danger",
    body: (name) => `“${name}” is removed. Its past runs are kept for the record. This can't be undone.`,
    done: (name) => `${name} deleted.`,
  },
};

function TableSkeleton() {
  return (
    <Card className="flex flex-col gap-4 p-6" aria-busy="true">
      <span className="sr-only" role="status">
        Loading automations
      </span>
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-48 w-full rounded-tile" />
    </Card>
  );
}

function RecipeList() {
  return (
    <section aria-labelledby="recipes-heading" className="flex flex-col gap-3">
      <h2 id="recipes-heading" className="text-lg font-bold">
        Start from a recipe
      </h2>
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {RECIPES.map((recipe) => (
          <li key={recipe.id}>
            <Link
              to={`/automations/new?recipe=${recipe.id}`}
              className="flex h-full flex-col gap-1 rounded-card bg-surface p-4 shadow-card transition-colors hover:bg-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <span className="text-sm font-bold text-ink">{recipe.name}</span>
              <span className="text-sm text-muted">{recipe.description}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function menuItems(
  automation: AutomationOut,
  open: (pending: PendingAction) => void,
  navigate: (to: string) => void,
): MenuItem[] {
  return [
    {
      label: automation.status === "draft" ? "Open builder" : "Open",
      onSelect: () => navigate(`/automations/${encodeURIComponent(automation.id)}`),
    },
    {
      label: "Pause",
      hidden: automation.status !== "active",
      onSelect: () => open({ action: "pause", automation }),
    },
    {
      label: "Resume",
      hidden: automation.status !== "paused",
      onSelect: () => open({ action: "resume", automation }),
    },
    {
      label: "Delete",
      tone: "danger",
      hidden: automation.status === "active",
      onSelect: () => open({ action: "delete", automation }),
    },
  ];
}

function AutomationRow({
  automation,
  triggerLabel,
  onAction,
}: {
  automation: AutomationOut;
  triggerLabel: string;
  onAction: (pending: PendingAction) => void;
}) {
  const navigate = useNavigate();
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <Link
          to={`/automations/${encodeURIComponent(automation.id)}`}
          className="text-sm font-bold text-ink underline-offset-4 hover:text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {automation.name}
        </Link>
        {automation.description !== "" ? (
          <span className="mt-1 block max-w-xs truncate text-xs text-muted">{automation.description}</span>
        ) : null}
      </th>
      <td className="py-3 pr-4 text-sm text-ink">{triggerLabel}</td>
      <td className="py-3 pr-4 text-sm text-muted">{automation.module}</td>
      <td className="py-3 pr-4">
        <AutomationStatusTag status={automation.status} />
      </td>
      <td className="py-3 pr-4 text-right text-sm tabular-nums text-ink">
        {formatNumber(automation.executions_30d)}
      </td>
      <td className="py-3 pr-4 text-right text-sm tabular-nums text-ink">
        {formatRate(automation.success_rate_30d)}
      </td>
      <td className="py-3 pr-4 text-sm text-muted">{formatDay(automation.updated_at)}</td>
      <td className="py-3 text-right">
        <Menu
          label={`More actions for ${automation.name}`}
          trigger="icon"
          icon={<MoreHorizontalIcon className="size-5" />}
          variant="ghost"
          align="end"
          items={menuItems(automation, onAction, navigate)}
        />
      </td>
    </tr>
  );
}

function Automations() {
  const navigate = useNavigate();
  const announce = useAnnounce();
  const titleRef = useRef<HTMLHeadingElement>(null);
  useRouteNotice(titleRef);

  const [tab, setTab] = useState<TabId>("all");
  const [search, setSearch] = useState("");
  const query = useDebouncedValue(search, 300);
  const [module, setModule] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  /* The trigger catalogue names the events and the modules to filter by. It is help: a failure
     here leaves raw event names and no module filter, and never blocks the list. */
  const triggerLoader = useCallback(
    () => listTriggers().catch(() => NO_TRIGGERS),
    [],
  );
  const triggerState = useAsync(triggerLoader, []);
  const triggers = triggerState.status === "success" ? triggerState.data : NO_TRIGGERS;
  const triggerLabels = useMemo(
    () => Object.fromEntries(triggers.map((t) => [t.event_type, t.label])),
    [triggers],
  );
  const moduleOptions = useMemo<SelectOption[]>(() => {
    const names = [...new Set(triggers.map((t) => t.module))].sort();
    if (module !== "" && !names.includes(module)) names.push(module);
    return [{ value: "", label: "All modules" }, ...names.map((m) => ({ value: m, label: m }))];
  }, [triggers, module]);

  const loader = useCallback(
    () =>
      listAutomations({
        ...(tab === "all" ? {} : { status: tab }),
        ...(module === "" ? {} : { module }),
        q: query,
        page,
        size,
      }),
    [tab, module, query, page, size],
  );
  const state = useAsync(loader, [tab, module, query, page, size]);
  const rows = state.status === "success" ? state.data.items : NO_ROWS;
  const filtered = tab !== "all" || module !== "" || query.trim() !== "";

  function closeDialog() {
    if (busy) return;
    setPending(null);
    setFailure(null);
  }

  async function confirm() {
    if (pending === null || busy) return;
    const { action, automation } = pending;
    setBusy(true);
    setFailure(null);
    try {
      if (action === "pause") await pauseAutomation(automation.id);
      else if (action === "resume") await resumeAutomation(automation.id);
      else await deleteAutomation(automation.id);
      setPending(null);
      announce(CONFIRM[action].done(automation.name));
      state.reload();
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function clearFilters() {
    setTab("all");
    setSearch("");
    setModule("");
    setPage(1);
  }

  const copy = pending === null ? null : CONFIRM[pending.action];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeading
        ref={titleRef}
        eyebrow="Communication"
        title="Automations"
        description="Create and manage the WhatsApp and email workflows that run when something happens in your business."
        actions={
          <>
            <Button variant="secondary" onClick={() => navigate("/automations/analytics")}>
              Analytics
            </Button>
            <Button onClick={() => navigate("/automations/new")}>Create automation</Button>
          </>
        }
      />

      <Tabs
        label="Automation status"
        items={TABS}
        value={tab}
        onChange={(id) => {
          setTab(id as TabId);
          setPage(1);
        }}
      >
        <div className="flex flex-col gap-6 pt-4">
          <Card className="flex flex-col gap-4 p-4 sm:p-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <SearchInput
                label="Search automations"
                placeholder="Search automations..."
                containerClassName="sm:max-w-xs sm:flex-1"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
              />
              <Select
                label="Module"
                containerClassName="sm:w-56"
                value={module}
                onChange={(event) => {
                  setModule(event.target.value);
                  setPage(1);
                }}
                options={moduleOptions}
              />
            </div>
          </Card>

        {state.status === "loading" ? <TableSkeleton /> : null}

        {state.status === "error" ? (
          <LoadError error={state.error} title="Couldn't load your automations" onRetry={state.reload} />
        ) : null}

        {state.status === "success" && rows.length === 0 ? (
          <>
            <Card className="p-6">
              <EmptyState
                icon={<SparkIcon className="size-6" />}
                title={filtered ? "No automations match" : "No automations yet"}
                description={
                  filtered
                    ? "Nothing matches these filters. Clear them to see every automation."
                    : "An automation sends a message or creates a task when something happens, like an order shipping or an invoice going overdue. Build one from scratch or start from a recipe below."
                }
                action={
                  filtered ? (
                    <Button variant="secondary" onClick={clearFilters}>
                      Clear filters
                    </Button>
                  ) : (
                    <Button onClick={() => navigate("/automations/new")}>Create automation</Button>
                  )
                }
              />
            </Card>
            {filtered ? null : <RecipeList />}
          </>
        ) : null}

        {state.status === "success" && rows.length > 0 ? (
          <Card className="p-6">
            <div className="overflow-x-auto">
              <table className="w-full min-w-4xl border-collapse text-left">
                <caption className="sr-only">
                  Automations, their trigger, module, status, runs and success rate over the last 30
                  days, and when each was last updated
                </caption>
                <thead>
                  <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                    <th scope="col" className="pb-2 pr-4">Name</th>
                    <th scope="col" className="pb-2 pr-4">Trigger</th>
                    <th scope="col" className="pb-2 pr-4">Module</th>
                    <th scope="col" className="pb-2 pr-4">Status</th>
                    <th scope="col" className="pb-2 pr-4 text-right">Executions (30 days)</th>
                    <th scope="col" className="pb-2 pr-4 text-right">Success rate</th>
                    <th scope="col" className="pb-2 pr-4">Updated</th>
                    <th scope="col" className="pb-2 text-right">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((automation) => (
                    <AutomationRow
                      key={automation.id}
                      automation={automation}
                      triggerLabel={triggerLabels[automation.trigger_event] ?? automation.trigger_event}
                      onAction={setPending}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="automations"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
              className="mt-4"
            />
          </Card>
        ) : null}
        </div>
      </Tabs>

      <ConfirmDialog
        open={pending !== null}
        onClose={closeDialog}
        onConfirm={() => void confirm()}
        title={copy?.title ?? ""}
        body={copy !== null && pending !== null ? copy.body(pending.automation.name) : ""}
        confirmLabel={copy?.confirm ?? ""}
        pendingLabel={copy?.pending ?? ""}
        tone={copy?.tone ?? "primary"}
        pending={busy}
        {...(failure !== null ? { error: failure } : {})}
      />
    </div>
  );
}

export function AutomationsPage() {
  return (
    <Announcer>
      <Automations />
    </Announcer>
  );
}
