import type { MilestoneOut, ProjectOut } from "@/api/types";
import { PHASE_LABEL } from "@/data/labels";
import { formatDate } from "@/lib/format";
import { Avatar } from "@opsgrids/ui";
import { HealthPill, MilestoneStatusPill, ProgressBar } from "./ProjectPills";

interface ProjectTimelineListProps {
  projects: ProjectOut[];
  milestones: MilestoneOut[];
}

/**
 * The timeline on a phone: a card per project in place of the Gantt, whose 216px label column and
 * month scale leave no room for a bar on a 360px screen. Everything the bars say is written out -
 * the dates, the health as a word, the progress and each dated milestone with its status.
 */
export function ProjectTimelineList({ projects, milestones }: ProjectTimelineListProps) {
  return (
    <ul aria-label="Projects on the timeline" className="flex flex-col gap-3">
      {projects.map((project) => {
        const dated = milestones
          .filter((m) => m.project_code === project.code && m.due_date !== null)
          .sort((a, b) => (a.due_date ?? "").localeCompare(b.due_date ?? ""));
        return (
          <li key={project.id} className="flex flex-col gap-3 rounded-card bg-surface p-4 shadow-card">
            <div className="flex items-start gap-3">
              <Avatar name={project.owner.name} size="sm" />
              <div className="flex min-w-0 flex-1 flex-col">
                <h3 className="break-words text-sm font-semibold text-ink">{project.name}</h3>
                <span className="tnum text-xs text-muted">
                  {project.code} · {PHASE_LABEL[project.phase]}
                </span>
              </div>
              <HealthPill health={project.health} />
            </div>

            <p className="tnum text-sm text-ink-soft">
              {formatDate(project.start_date)} to {formatDate(project.target_date)}
            </p>
            <ProgressBar value={project.progress_pct} health={project.health} showLabel />

            {dated.length > 0 ? (
              <div className="flex flex-col gap-2 border-t border-line pt-3">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">Milestones</h4>
                <ul className="flex flex-col gap-2">
                  {dated.map((m) => (
                    <li key={m.id} className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 flex-col">
                        <span className="break-words text-sm text-ink">{m.title}</span>
                        <span className="tnum text-xs text-muted">{formatDate(m.due_date ?? "")}</span>
                      </div>
                      <MilestoneStatusPill status={m.status} />
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
