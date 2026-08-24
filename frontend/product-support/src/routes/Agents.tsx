import { useMemo } from "react";

import { getSlaPolicy, listAgents } from "@/api/client";
import type { Agent, Priority } from "@/api/types";
import { cn } from "@/lib/cn";
import { formatMinutes, priorityLabel } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  Avatar,
  Card,
  DataTable,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { GaugeIcon } from "@/ui/icons";

const ROLE_LABEL: Record<Agent["role"], string> = {
  agent: "Agent",
  senior_agent: "Senior Agent",
  team_lead: "Team Lead",
};

const PRIORITY_DOT: Record<Priority, string> = {
  urgent: "bg-danger",
  high: "bg-late",
  normal: "bg-info",
  low: "bg-faint",
};

/** Online / Away, labelled so the state never rests on the dot's colour. */
function PresencePill({ online }: { online: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
        online ? "bg-present-soft text-present" : "bg-ground text-muted",
      )}
    >
      <span
        className={cn("size-1.5 rounded-full", online ? "bg-present" : "bg-faint")}
        aria-hidden
      />
      {online ? "Online" : "Away"}
    </span>
  );
}

/** A compact CSAT meter — a labelled percentage with a proportional track. */
function CsatMeter({ pct }: { pct: number }) {
  return (
    <div className="flex items-center justify-end gap-2">
      <div className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-ground sm:block">
        <div className="h-full rounded-full bg-present" style={{ width: `${pct}%` }} />
      </div>
      <span className="tnum font-semibold text-ink">{pct}%</span>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
      <span className="text-xs font-semibold uppercase tracking-wide text-faint">{label}</span>
      <span className="display text-2xl font-extrabold tnum">{value}</span>
    </div>
  );
}

export function AgentsPage() {
  const agentsState = useAsync(listAgents, []);
  const policyState = useAsync(getSlaPolicy, []);

  const agents = useMemo(
    () => (agentsState.status === "success" ? agentsState.data : []),
    [agentsState],
  );

  const summary = useMemo(() => {
    if (agents.length === 0) return null;
    const load = agents.reduce((s, a) => s + a.open_load, 0);
    const csat = Math.round(agents.reduce((s, a) => s + a.csat_pct, 0) / agents.length);
    const online = agents.filter((a) => a.online).length;
    const solved = agents.reduce((s, a) => s + a.solved_7d, 0);
    return { load, csat, online, solved };
  }, [agents]);

  const columns: Column<Agent>[] = useMemo(
    () => [
      {
        key: "agent",
        header: "Agent",
        render: (a) => (
          <div className="flex items-center gap-3">
            <Avatar name={a.name} size="md" />
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate font-semibold text-ink">{a.name}</span>
              <span className="truncate text-xs text-muted">
                {ROLE_LABEL[a.role]} · {a.team}
              </span>
            </div>
          </div>
        ),
      },
      {
        key: "presence",
        header: "Status",
        headerClassName: "hidden sm:table-cell",
        cellClassName: "hidden sm:table-cell",
        render: (a) => <PresencePill online={a.online} />,
      },
      {
        key: "load",
        header: "Open load",
        align: "right",
        render: (a) => <span className="tnum font-semibold text-ink">{a.open_load}</span>,
      },
      {
        key: "frt",
        header: "Avg response",
        align: "right",
        headerClassName: "hidden md:table-cell",
        cellClassName: "hidden md:table-cell",
        render: (a) => (
          <span className="tnum text-ink-soft">{formatMinutes(a.avg_first_response_min)}</span>
        ),
      },
      {
        key: "solved",
        header: "Solved · 7d",
        align: "right",
        headerClassName: "hidden lg:table-cell",
        cellClassName: "hidden lg:table-cell",
        render: (a) => <span className="tnum text-ink-soft">{a.solved_7d}</span>,
      },
      {
        key: "csat",
        header: "CSAT",
        align: "right",
        render: (a) => <CsatMeter pct={a.csat_pct} />,
      },
    ],
    [],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Support"
        title="SLA & Agents"
        actions={
          <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
            <GaugeIcon className="size-4 text-muted" />
            <span className="tnum">{agents.length}</span>
            <span className="font-medium text-muted">agents</span>
          </span>
        }
      />

      {/* Summary tiles */}
      {summary ? (
        <section
          aria-label="Team summary"
          className="grid grid-cols-2 gap-4 sm:grid-cols-4"
        >
          <StatTile label="Online now" value={`${summary.online}/${agents.length}`} />
          <StatTile label="Open load" value={String(summary.load)} />
          <StatTile label="Avg CSAT" value={`${summary.csat}%`} />
          <StatTile label="Solved · 7d" value={String(summary.solved)} />
        </section>
      ) : null}

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-3">
        {/* Agents table */}
        <Card className="p-2 sm:p-4 xl:col-span-2">
          <div className="px-3 pt-3 sm:px-2">
            <SectionHeader title="Agents" subtitle="Load and quality over the last 7 days" />
          </div>
          {agentsState.status === "loading" ? (
            <div className="flex flex-col gap-2 p-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {agentsState.status === "error" ? (
            <ErrorState message={agentsState.error.message} />
          ) : null}
          {agentsState.status === "success" ? (
            <div className="mt-2">
              <DataTable
                columns={columns}
                rows={agents}
                rowKey={(a) => a.id}
                caption="Support agents and their current load and quality"
              />
            </div>
          ) : null}
        </Card>

        {/* SLA policy */}
        <Card className="flex flex-col gap-5 p-6">
          {policyState.status === "loading" ? (
            <Skeleton className="h-72 w-full rounded-tile" />
          ) : null}
          {policyState.status === "error" ? (
            <ErrorState message={policyState.error.message} />
          ) : null}
          {policyState.status === "success" ? (
            <>
              <SectionHeader
                title="SLA policy"
                subtitle={policyState.data.name}
              />
              <dl className="flex flex-col gap-3 text-sm">
                <div className="flex flex-col gap-0.5">
                  <dt className="text-xs font-semibold uppercase tracking-wide text-faint">
                    Business hours
                  </dt>
                  <dd className="font-semibold text-ink">{policyState.data.business_hours}</dd>
                </div>
                <div className="flex flex-col gap-0.5">
                  <dt className="text-xs font-semibold uppercase tracking-wide text-faint">
                    Coverage
                  </dt>
                  <dd className="text-ink-soft leading-relaxed">{policyState.data.coverage}</dd>
                </div>
              </dl>

              <div className="flex flex-col gap-2 border-t border-line pt-4">
                <span className="text-xs font-semibold uppercase tracking-wide text-faint">
                  Targets by priority
                </span>
                <table className="w-full text-sm">
                  <caption className="sr-only">SLA targets by priority</caption>
                  <thead>
                    <tr className="text-xs font-semibold uppercase tracking-wide text-faint">
                      <th scope="col" className="py-2 text-left font-semibold">Priority</th>
                      <th scope="col" className="py-2 text-right font-semibold">1st reply</th>
                      <th scope="col" className="py-2 text-right font-semibold">Resolve</th>
                    </tr>
                  </thead>
                  <tbody>
                    {policyState.data.targets.map((t) => (
                      <tr key={t.priority} className="border-t border-line/70">
                        <td className="py-2.5">
                          <span className="inline-flex items-center gap-2 font-semibold text-ink">
                            <span
                              className={cn("size-2 rounded-full", PRIORITY_DOT[t.priority])}
                              aria-hidden
                            />
                            {priorityLabel(t.priority)}
                          </span>
                        </td>
                        <td className="py-2.5 text-right tnum text-ink-soft">
                          {formatMinutes(t.first_response_min)}
                        </td>
                        <td className="py-2.5 text-right tnum text-ink-soft">
                          {formatMinutes(t.resolution_min)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
