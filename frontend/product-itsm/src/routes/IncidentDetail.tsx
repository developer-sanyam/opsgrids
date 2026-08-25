import { useCallback } from "react";
import type { ComponentType, SVGProps } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getIncident } from "@/api/client";
import type { WorklogEntry } from "@/api/types";
import {
  formatDateTime,
  formatSlaClock,
  incidentCategoryLabel,
  relativeTime,
} from "@/lib/format";
import { useAsync } from "@atmora/client";
import {
  Avatar,
  Card,
  ErrorState,
  IncidentStatusPill,
  PriorityPill,
  SectionHeader,
  Skeleton,
  SlaPill,
} from "@/ui";
import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  BoxIcon,
  CheckCircleIcon,
  ClockIcon,
  MessageIcon,
  ShieldCheckIcon,
  UserIcon,
} from "@atmora/ui";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

/** Worklog kind → glyph + tint, so the thread scans as a timeline of typed events. */
const KIND_META: Record<WorklogEntry["kind"], { tone: string; icon: IconType }> = {
  note: { tone: "bg-ground text-muted", icon: MessageIcon },
  assignment: { tone: "bg-accent-soft text-accent-ink", icon: UserIcon },
  status: { tone: "bg-info-soft text-info", icon: ClockIcon },
  sla: { tone: "bg-danger-soft text-danger", icon: ShieldCheckIcon },
  resolution: { tone: "bg-present-soft text-present", icon: CheckCircleIcon },
};

function WorklogThread({ entries }: { entries: WorklogEntry[] }) {
  // Newest first reads like a live desk feed.
  const ordered = [...entries].sort((a, b) => b.at.localeCompare(a.at));
  return (
    <ol className="flex flex-col">
      {ordered.map((entry, i) => {
        const meta = KIND_META[entry.kind];
        const Icon = meta.icon;
        const last = i === ordered.length - 1;
        return (
          <li key={entry.id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span className={`grid size-9 shrink-0 place-items-center rounded-full ${meta.tone}`}>
                <Icon className="size-[18px]" />
              </span>
              {!last ? <span className="w-px flex-1 bg-line" aria-hidden /> : null}
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-0.5 pb-6">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-sm font-bold text-ink">{entry.author}</span>
                <span className="tnum text-xs text-faint" title={formatDateTime(entry.at)}>
                  {relativeTime(entry.at)}
                </span>
              </div>
              <p className="text-sm text-ink-soft leading-relaxed">{entry.body}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function PropertyRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-sm font-semibold text-ink text-right">{children}</span>
    </div>
  );
}

export function IncidentDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const loader = useCallback(() => getIncident(id), [id]);
  const state = useAsync(loader, [id]);

  return (
    <div className="rise flex flex-col gap-5">
      <button
        type="button"
        onClick={() => navigate("/incidents")}
        className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to incidents
      </button>

      {state.status === "loading" ? (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <Card className="p-6 xl:col-span-2">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="mt-4 h-40 w-full rounded-tile" />
          </Card>
          <Card className="p-6">
            <Skeleton className="h-40 w-full rounded-tile" />
          </Card>
        </div>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            message={state.error.message}
            onRetry={() => navigate("/incidents")}
          />
        </Card>
      ) : null}

      {state.status === "success" ? (
        (() => {
          const incident = state.data;
          return (
            <>
              <header className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="tnum text-sm font-semibold text-muted">{incident.id}</span>
                  <PriorityPill priority={incident.priority} />
                  <IncidentStatusPill status={incident.status} />
                  <SlaPill state={incident.sla_state} />
                </div>
                <h1 className="display text-2xl sm:text-3xl font-extrabold leading-tight">
                  {incident.summary}
                </h1>
              </header>

              <div className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
                {/* Left — description + activity thread */}
                <div className="flex flex-col gap-5 xl:col-span-2">
                  <Card className="flex flex-col gap-3 p-6">
                    <SectionHeader title="Description" />
                    <p className="text-sm text-ink-soft leading-relaxed">{incident.description}</p>
                  </Card>

                  <Card className="flex flex-col gap-5 p-6">
                    <SectionHeader
                      title="Activity"
                      subtitle={`${incident.worklog.length} entries · worklog`}
                    />
                    <WorklogThread entries={incident.worklog} />
                  </Card>
                </div>

                {/* Right — properties + affected asset */}
                <div className="flex flex-col gap-5">
                  <Card className="flex flex-col p-6">
                    <SectionHeader title="Properties" />
                    <dl className="mt-2 divide-y divide-line">
                      <PropertyRow label="Priority">
                        <PriorityPill priority={incident.priority} />
                      </PropertyRow>
                      <PropertyRow label="Status">
                        <IncidentStatusPill status={incident.status} />
                      </PropertyRow>
                      <PropertyRow label="SLA">
                        <span className="inline-flex flex-col items-end gap-1">
                          <SlaPill state={incident.sla_state} />
                          {incident.sla_minutes_remaining !== null ? (
                            <span className="tnum text-xs font-medium text-muted">
                              {formatSlaClock(incident.sla_minutes_remaining)}
                            </span>
                          ) : null}
                        </span>
                      </PropertyRow>
                      <PropertyRow label="Category">
                        {incidentCategoryLabel(incident.category)}
                      </PropertyRow>
                      <PropertyRow label="Assignee">
                        <span className="inline-flex items-center gap-2">
                          <Avatar name={incident.assignee} size="sm" />
                          {incident.assignee}
                        </span>
                      </PropertyRow>
                      <PropertyRow label="Requester">{incident.requester}</PropertyRow>
                      <PropertyRow label="Opened">
                        <span className="tnum font-medium text-muted">
                          {formatDateTime(incident.created_at)}
                        </span>
                      </PropertyRow>
                      <PropertyRow label="Updated">
                        <span className="tnum font-medium text-muted">
                          {formatDateTime(incident.updated_at)}
                        </span>
                      </PropertyRow>
                    </dl>
                  </Card>

                  <Card className="flex flex-col gap-3 p-6">
                    <SectionHeader title="Affected asset" />
                    {incident.affected_asset_tag ? (
                      <Link
                        to={`/assets/${incident.affected_asset_tag}`}
                        className="group flex items-center gap-3 rounded-tile bg-ground px-4 py-3 transition-colors hover:bg-line"
                      >
                        <span className="grid size-10 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
                          <BoxIcon className="size-5" />
                        </span>
                        <span className="flex min-w-0 flex-col leading-tight">
                          <span className="tnum text-sm font-bold text-ink">
                            {incident.affected_asset_tag}
                          </span>
                          <span className="text-xs text-muted">View in the CMDB</span>
                        </span>
                      </Link>
                    ) : (
                      <p className="flex items-center gap-2 text-sm text-muted">
                        <AlertTriangleIcon className="size-4 text-faint" />
                        No specific configuration item is linked to this incident.
                      </p>
                    )}
                  </Card>
                </div>
              </div>
            </>
          );
        })()
      ) : null}
    </div>
  );
}
