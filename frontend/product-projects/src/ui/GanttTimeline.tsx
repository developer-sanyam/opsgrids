import { useEffect, useMemo, useRef } from "react";

import type { MilestoneOut, ProjectOut } from "@/api/types";
import { HEALTH_LABEL, MILESTONE_STATUS_LABEL, PHASE_LABEL } from "@/data/labels";
import { formatDate } from "@/lib/format";
import { Avatar } from "@opsgrids/ui";
import { healthColor } from "./healthColor";

const DAY_PX = 3.4;
const LABEL_W = 216;
const ROW_H = 48;
const HEADER_H = 34;

const MS_PER_DAY = 86_400_000;

function parseUtc(iso: string): number {
  return Date.parse(`${iso}T00:00:00Z`);
}

function daysFrom(startMs: number, iso: string): number {
  return Math.round((parseUtc(iso) - startMs) / MS_PER_DAY);
}

function firstOfMonth(ms: number): number {
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
}

function addMonth(ms: number): number {
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
}

const MILESTONE_COLOR: Record<MilestoneOut["status"], string> = {
  completed: "var(--color-present)",
  on_track: "var(--color-info)",
  due_soon: "var(--color-cyan)",
  at_risk: "var(--color-late)",
  slipping: "var(--color-danger)",
};

/** A milestone known to have a due date — the only kind this timeline can place. */
type DatedMilestone = Omit<MilestoneOut, "due_date"> & { due_date: string };

interface GanttTimelineProps {
  projects: ProjectOut[];
  milestones: MilestoneOut[];
  /** The real "today" (`lib/format.ts#today()`), never the frozen fixture constant in live mode. */
  now: string;
}

/**
 * A Gantt-style timeline: one row per project, bars spanning start→target across a month scale,
 * with milestone diamonds pinned to their due dates and a "Today" marker. The whole grid scrolls
 * horizontally inside its own container; the project-label column stays pinned to the left.
 */
export function GanttTimeline({ projects, milestones, now }: GanttTimelineProps) {
  const geo = useMemo(() => {
    const starts = projects.map((p) => parseUtc(p.start_date));
    const ends = projects.map((p) => parseUtc(p.target_date));
    const domainStart = firstOfMonth(Math.min(...starts, parseUtc(now)));
    const domainEnd = addMonth(Math.max(...ends, parseUtc(now)));
    const totalDays = Math.round((domainEnd - domainStart) / MS_PER_DAY);
    const trackW = totalDays * DAY_PX;

    const months: { label: string; x: number }[] = [];
    for (let m = domainStart; m < domainEnd; m = addMonth(m)) {
      const d = new Date(m);
      months.push({
        label: d.toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "UTC" }),
        x: daysFrom(domainStart, new Date(m).toISOString().slice(0, 10)) * DAY_PX,
      });
    }

    const todayX = daysFrom(domainStart, now) * DAY_PX;

    return { domainStart, trackW, months, todayX };
  }, [projects, now]);

  // An undated milestone has nothing to place on a date axis — the backend's own `/timeline`
  // route drops the same rows (`routes.py`'s `_week_axis`/milestone loop skips a null `due_date`).
  const milestonesByProject = useMemo(() => {
    const map = new Map<string, DatedMilestone[]>();
    for (const m of milestones) {
      if (m.due_date === null) continue;
      const dated: DatedMilestone = { ...m, due_date: m.due_date };
      const list = map.get(m.project_code) ?? [];
      list.push(dated);
      map.set(m.project_code, list);
    }
    return map;
  }, [milestones]);

  const x = (iso: string) => daysFrom(geo.domainStart, iso) * DAY_PX;

  const scrollRef = useRef<HTMLDivElement>(null);

  // Open on the current period: scroll so "Today" sits a third of the way in, where the active
  // bars and their imminent milestones are, rather than on the near-empty earliest months.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const target = LABEL_W + geo.todayX - el.clientWidth / 3;
    el.scrollTo({ left: Math.max(0, target), behavior: "auto" });
  }, [geo.todayX]);

  return (
    <div className="flex flex-col gap-4">
      <div ref={scrollRef} className="overflow-x-auto rounded-tile border border-line">
        <div style={{ width: LABEL_W + geo.trackW, minWidth: "100%" }}>
          {/* Header: month scale */}
          <div
            className="sticky top-0 z-10 flex border-b border-line bg-surface"
            style={{ height: HEADER_H }}
          >
            <div
              className="sticky left-0 z-20 flex items-center border-r border-line bg-surface px-3 text-[0.7rem] font-bold uppercase tracking-wider text-faint"
              style={{ width: LABEL_W }}
            >
              Project
            </div>
            <div className="relative" style={{ width: geo.trackW }}>
              {geo.months.map((month) => (
                <div
                  key={month.x}
                  className="absolute top-0 flex h-full items-center border-l border-line/70 pl-1.5 text-[0.7rem] font-semibold text-muted"
                  style={{ left: month.x }}
                >
                  {month.label}
                </div>
              ))}
            </div>
          </div>

          {/* Rows */}
          <div className="relative">
            {/* Today marker spans all rows */}
            {geo.todayX >= 0 && geo.todayX <= geo.trackW ? (
              <div
                className="pointer-events-none absolute z-10"
                style={{ left: LABEL_W + geo.todayX, top: 0, bottom: 0 }}
              >
                <div className="h-full w-px bg-accent" />
                <span className="absolute -top-0.5 left-0 -translate-x-1/2 rounded-full bg-accent px-1.5 py-0.5 text-[0.6rem] font-bold text-white">
                  Today
                </span>
              </div>
            ) : null}

            {projects.map((project) => {
              const left = x(project.start_date);
              const width = Math.max(x(project.target_date) - left, 8);
              const projectMilestones = milestonesByProject.get(project.code) ?? [];
              const color = healthColor(project.health);
              return (
                <div
                  key={project.id}
                  className="flex border-b border-line/60 last:border-0 hover:bg-ground/40"
                  style={{ height: ROW_H }}
                >
                  <div
                    className="sticky left-0 z-[5] flex items-center gap-2.5 border-r border-line bg-surface px-3"
                    style={{ width: LABEL_W }}
                  >
                    <Avatar name={project.owner.name} size="xs" />
                    <div className="flex min-w-0 flex-col leading-tight">
                      <span className="truncate text-xs font-bold text-ink">{project.name}</span>
                      <span className="truncate text-[0.7rem] text-muted tnum">
                        {project.code} · {PHASE_LABEL[project.phase]}
                      </span>
                    </div>
                  </div>

                  <div className="relative" style={{ width: geo.trackW }}>
                    {/* Bar */}
                    <div
                      className="absolute top-1/2 flex -translate-y-1/2 items-center overflow-hidden rounded-full"
                      style={{
                        left,
                        width,
                        height: 22,
                        backgroundColor: color,
                        opacity: 0.22,
                      }}
                      aria-hidden
                    />
                    <div
                      className="absolute top-1/2 -translate-y-1/2 rounded-full"
                      style={{
                        left,
                        width: Math.max((width * project.progress_pct) / 100, 4),
                        height: 22,
                        backgroundColor: color,
                      }}
                      title={`${project.name}: ${formatDate(project.start_date)} → ${formatDate(project.target_date)}, ${HEALTH_LABEL[project.health]}, ${project.progress_pct}% complete`}
                    />
                    <span
                      className="absolute top-1/2 -translate-y-1/2 text-[0.65rem] font-bold tnum text-white mix-blend-luminosity"
                      style={{ left: left + 8 }}
                    >
                      {project.progress_pct}%
                    </span>

                    {/* Milestone diamonds */}
                    {projectMilestones.map((m) => (
                      <span
                        key={m.id}
                        className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px] ring-2 ring-surface"
                        style={{ left: x(m.due_date), backgroundColor: MILESTONE_COLOR[m.status] }}
                        title={`${m.title} — ${formatDate(m.due_date)} (${MILESTONE_STATUS_LABEL[m.status]})`}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted">
        <span className="font-semibold uppercase tracking-wide text-faint">Health</span>
        {(["on_track", "at_risk", "delayed"] as const).map((h) => (
          <span key={h} className="inline-flex items-center gap-1.5">
            <span className="h-2 w-4 rounded-full" style={{ backgroundColor: healthColor(h) }} aria-hidden />
            {HEALTH_LABEL[h]}
          </span>
        ))}
        <span className="ml-2 inline-flex items-center gap-1.5">
          <span className="size-2.5 rotate-45 rounded-[2px] bg-info" aria-hidden />
          Milestone
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-3 w-px bg-accent" aria-hidden />
          Today
        </span>
      </div>
    </div>
  );
}
