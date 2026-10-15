import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { UNKNOWN_MEMBER_NAME, problemMessage, useAsync, useAuth, useCan, usePermissions } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  FilterBar,
  InlineAlert,
  Skeleton,
  Tag,
  TextField,
  formatInZone,
  wallClockToInstant,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { listAuditLog, listMembers } from "@/api";
import type { AuditEntry, AuditQuery } from "@/api/types";
import {
  activeSources,
  describeAction,
  everySourceRefused,
  initialStates,
  nextPage,
  sourceLabel,
} from "@/lib/auditFeed";
import type { SourceState } from "@/lib/auditFeed";
import { PageHeader } from "@/ui";

const PAGE_SIZE = 50;
const REVEAL_ACTION = "entity.sensitive_read";

/** The viewer's own zone: every instant here is shown in it, and the text names it. */
function viewerZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** `yyyy-mm-dd` plus one calendar day, so a "to" date includes the whole of that day. */
function dayAfter(date: string): string {
  const [y, m, d] = date.split("-").map(Number);
  const next = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, (d ?? 1) + 1));
  return next.toISOString().slice(0, 10);
}

function shortId(id: string | null): string {
  return id ? id.slice(0, 8) : "";
}

type Load =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; entries: AuditEntry[]; states: SourceState[]; hasMore: boolean; loadingMore: boolean };

export function AuditLogPage() {
  const { entitlements } = usePermissions();
  const canReadMembers = useCan("core.identity.membership.read");
  const { user } = useAuth();
  const zone = useMemo(viewerZone, []);

  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [actorId, setActorId] = useState("");
  const [service, setService] = useState("");
  const [kind, setKind] = useState("");
  // FilterBar's search debounces for us: this is the committed value.
  const [rawAction, setRawAction] = useState("");
  const action = rawAction.trim();
  const [attempt, setAttempt] = useState(0);

  const sources = useMemo(() => activeSources(entitlements), [entitlements]);
  const chosen = useMemo(
    () => (service ? sources.filter((s) => s.service === service) : sources),
    [sources, service],
  );

  const people = useAsync(
    async () => (canReadMembers ? (await listMembers({ size: 200 })).items : []),
    [canReadMembers],
  );

  // One directory read serves both the Person filter and the names in the table; without
  // `core.identity.membership.read` everyone but the caller reads as "a team member", never an id.
  const names = useMemo(() => {
    const map = new Map<string, string>(
      people.status === "success" ? people.data.map((m) => [m.user_id, m.display_name ?? m.full_name]) : [],
    );
    if (user) map.set(user.user_id, user.full_name);
    return map;
  }, [people, user]);

  const rangeError = fromDate && toDate && fromDate > toDate ? "The start date is after the end date." : undefined;

  const filters = useMemo((): Omit<AuditQuery, "cursor" | "limit"> => {
    const f: Omit<AuditQuery, "cursor" | "limit"> = {};
    if (fromDate) f.from = wallClockToInstant(`${fromDate}T00:00`, zone);
    if (toDate) f.to = wallClockToInstant(`${dayAfter(toDate)}T00:00`, zone);
    if (actorId) f.actor_id = actorId;
    if (kind === "reveals") f.action = REVEAL_ACTION;
    else if (action) f.action = action;
    return f;
  }, [fromDate, toDate, actorId, kind, action, zone]);

  const [load, setLoad] = useState<Load>({ status: "loading" });
  // A newer filter change wins over a slower response for an older one.
  const generation = useRef(0);

  useEffect(() => {
    if (rangeError) return;
    const mine = ++generation.current;
    setLoad({ status: "loading" });
    nextPage(initialStates(chosen), PAGE_SIZE, filters, listAuditLog).then(
      (page) => {
        if (generation.current !== mine) return;
        setLoad({ status: "ready", entries: page.entries, states: page.states, hasMore: page.hasMore, loadingMore: false });
      },
      (error: unknown) => {
        if (generation.current === mine) setLoad({ status: "error", error });
      },
    );
  }, [chosen, filters, rangeError, attempt]);

  const loadMore = useCallback(() => {
    if (load.status !== "ready" || !load.hasMore || load.loadingMore) return;
    const mine = generation.current;
    setLoad({ ...load, loadingMore: true });
    nextPage(load.states, PAGE_SIZE, filters, listAuditLog).then(
      (page) => {
        if (generation.current !== mine) return;
        setLoad({
          status: "ready",
          entries: [...load.entries, ...page.entries],
          states: page.states,
          hasMore: page.hasMore,
          loadingMore: false,
        });
      },
      (error: unknown) => {
        if (generation.current === mine) setLoad({ status: "error", error });
      },
    );
  }, [load, filters]);

  const filtered = Boolean(fromDate || toDate || actorId || service || kind || action);

  function clearFilters() {
    setFromDate("");
    setToDate("");
    setActorId("");
    setService("");
    setKind("");
    setRawAction("");
  }

  const actor = (e: AuditEntry) => {
    if (e.by_operator) return "OpsGrids support";
    if (!e.actor_id) return "System";
    return names.get(e.actor_id) ?? UNKNOWN_MEMBER_NAME;
  };

  const columns: Column<AuditEntry>[] = [
    {
      key: "what",
      header: "Activity",
      mobile: "title",
      render: (e) => (
        <div className="flex flex-col gap-1">
          <span className="font-semibold text-ink">
            {e.sensitive ? `Revealed ${e.fields.length > 0 ? e.fields.join(", ") : "personal data"}` : describeAction(e.action)}
          </span>
          <span className="text-xs text-muted">
            {e.entity}
            {e.entity_id ? ` ${shortId(e.entity_id)}` : ""}
          </span>
        </div>
      ),
    },
    {
      key: "kind",
      header: "Type",
      mobile: "status",
      render: (e) => (e.sensitive ? <Tag tone="warning">Personal data</Tag> : <Tag>Change</Tag>),
    },
    { key: "who", header: "Who", render: (e) => actor(e) },
    {
      key: "when",
      header: "When",
      cellClassName: "tabular-nums whitespace-nowrap",
      render: (e) => <time dateTime={e.at}>{formatInZone(e.at, zone)}</time>,
    },
    { key: "where", header: "Product", render: (e) => sourceLabel(e.service) },
    {
      key: "fields",
      header: "Fields",
      render: (e) =>
        e.fields.length === 0 ? (
          <span className="text-faint">None</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {e.fields.map((f) => (
              <Tag key={f}>{f}</Tag>
            ))}
          </div>
        ),
    },
    { key: "reason", header: "Reason", render: (e) => e.reason ?? <span className="text-faint">None</span> },
  ];

  const failed = load.status === "ready" ? load.states.filter((s) => s.error !== null) : [];
  const refused = load.status === "ready" && everySourceRefused(load.states);

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Administration"
        title="Audit log"
        description="Who did what in your organisation, and when. Personal-data reveals show which field was opened, never its value."
      />

      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-3 sm:max-w-xl sm:grid-cols-2">
          <TextField label="From" type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          <TextField label="To" type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} error={rangeError} />
        </div>
        <FilterBar
          label="Filter the audit log"
          search={
            kind === "reveals"
              ? undefined
              : {
                  label: "Action",
                  placeholder: "e.g. member.invited",
                  value: rawAction,
                  onChange: setRawAction,
                }
          }
          filters={[
            {
              key: "person",
              label: "Person",
              allLabel: "Everyone",
              options:
                people.status === "success"
                  ? people.data.map((m) => ({ value: m.user_id, label: m.display_name ?? m.full_name }))
                  : [],
              value: actorId,
              onChange: setActorId,
            },
            {
              key: "product",
              label: "Product",
              allLabel: "All products",
              options: sources.map((s) => ({ value: s.service, label: s.label })),
              value: service,
              onChange: setService,
            },
            {
              key: "type",
              label: "Type",
              allLabel: "All activity",
              options: [{ value: "reveals", label: "Personal-data reveals" }],
              value: kind,
              onChange: setKind,
            },
          ]}
          chips={[
            ...(fromDate ? [{ key: "from", label: `From: ${fromDate}`, onRemove: () => setFromDate("") }] : []),
            ...(toDate ? [{ key: "to", label: `To: ${toDate}`, onRemove: () => setToDate("") }] : []),
          ]}
          onClearAll={clearFilters}
        />
        <p className="text-xs text-muted">Times are shown in {zone}.</p>
      </div>

      {load.status === "loading" && !rangeError ? <Skeleton className="h-80 w-full rounded-card" /> : null}

      {load.status === "error" ? (
        <ErrorState
          title="Couldn't load the audit log"
          message={problemMessage(load.error)}
          onRetry={() => setAttempt((n) => n + 1)}
        />
      ) : null}

      {refused ? (
        <ErrorState
          title="You don't have access to the audit log"
          message="Reading the audit log needs the organisation-wide audit permission. Ask your organisation's owner."
        />
      ) : null}

      {load.status === "ready" && !refused ? (
        <>
          {failed.length > 0 ? (
            <InlineAlert
              tone="warning"
              title={`Some activity couldn't be loaded: ${failed.map((s) => sourceLabel(s.service)).join(", ")}.`}
              action={
                <Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
                  Try again
                </Button>
              }
            />
          ) : null}
          <Card className="p-0">
            <DataTable
              rows={load.entries}
              rowKey={(e) => `${e.service}:${e.id}`}
              columns={columns}
              mobileCard="auto"
              emptyState={
                <EmptyState
                  title={filtered ? "No activity matches these filters" : "No activity recorded yet"}
                  description={
                    filtered
                      ? "Widen the date range or clear a filter."
                      : "Changes your team makes, and every personal-data reveal, will be listed here."
                  }
                  action={
                    filtered ? (
                      <Button variant="outline" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    ) : null
                  }
                />
              }
            />
          </Card>
          {load.hasMore ? (
            <div className="flex justify-center">
              <Button variant="outline" onClick={loadMore} loading={load.loadingMore}>
                Load older activity
              </Button>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
