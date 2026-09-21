import { getAllMilestones } from "@/api/client";
import type { ProjectOut } from "@/api/types";
import { sortByDueDate } from "@/data/derive";
import { formatDate, formatMoney, relativeDue, today } from "@/lib/format";
import { Can, useAsync } from "@opsgrids/client";
import { Avatar, Button, Dialog, ErrorState, HealthPill, MilestoneStatusPill, PhasePill, ProgressBar, Skeleton } from "@/ui";

const WRITE = "projects.portfolio.project.write";

/**
 * The drill-down for one project: health, progress, budget, dates, team and its milestones, in a
 * real `Dialog` — focus trapped, initial focus on open, focus returned to the row that opened it
 * on close. Used at every width, so a row tap never depends on a sticky side panel that only
 * fits above `xl`.
 */
export function ProjectDetailDialog({
  project,
  onClose,
  onEdit,
  onDelete,
  onAddMilestone,
}: {
  project: ProjectOut;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onAddMilestone: () => void;
}) {
  const milestones = useAsync(
    () => getAllMilestones({ project_code: project.code }),
    [project.code],
  );
  const spentPct =
    project.budget_minor === 0 ? 0 : Math.round((project.spent_minor / project.budget_minor) * 100);

  return (
    <Dialog
      open
      onClose={onClose}
      title={project.name}
      description={
        <span className="inline-flex items-center gap-2">
          <span className="tnum">{project.code}</span>
          {project.client ? <span>· {project.client}</span> : null}
        </span>
      }
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Can permission={WRITE}>
            <Button variant="danger" onClick={onDelete}>
              Delete
            </Button>
            <Button onClick={onEdit}>Edit</Button>
          </Can>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {project.summary ? (
          <p className="text-sm leading-relaxed text-ink-soft">{project.summary}</p>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <HealthPill health={project.health} />
          <PhasePill phase={project.phase} />
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted">Delivery progress</span>
            <span className="font-semibold text-ink tnum">{project.progress_pct}%</span>
          </div>
          <ProgressBar value={project.progress_pct} health={project.health} />
        </div>

        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div className="rounded-tile bg-ground px-3 py-2.5">
            <dt className="text-xs text-muted">Owner</dt>
            <dd className="flex items-center gap-2 font-semibold text-ink">
              <Avatar name={project.owner.name} size="xs" />
              {project.owner.name}
            </dd>
          </div>
          <div className="rounded-tile bg-ground px-3 py-2.5">
            <dt className="text-xs text-muted">Team size</dt>
            <dd className="font-semibold text-ink tnum">{project.team_size} people</dd>
          </div>
          <div className="rounded-tile bg-ground px-3 py-2.5">
            <dt className="text-xs text-muted">Start</dt>
            <dd className="font-semibold text-ink tnum">{formatDate(project.start_date)}</dd>
          </div>
          <div className="rounded-tile bg-ground px-3 py-2.5">
            <dt className="text-xs text-muted">Target</dt>
            <dd className="font-semibold text-ink tnum">{formatDate(project.target_date)}</dd>
          </div>
          <div className="rounded-tile bg-ground px-3 py-2.5">
            <dt className="text-xs text-muted">Budget</dt>
            <dd className="font-semibold text-ink tnum">
              {formatMoney(project.budget_minor, project.currency)}
            </dd>
          </div>
          <div className="rounded-tile bg-ground px-3 py-2.5">
            <dt className="text-xs text-muted">Spent</dt>
            <dd className="font-semibold text-ink tnum">
              {formatMoney(project.spent_minor, project.currency)} · {spentPct}%
            </dd>
          </div>
        </dl>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-ink">Milestones</h3>
            <Can permission="projects.delivery.milestone.write">
              <Button variant="outline" size="sm" onClick={onAddMilestone}>
                Add milestone
              </Button>
            </Can>
          </div>
          {milestones.status === "loading" ? (
            <div className="flex flex-col gap-1.5">
              {Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full rounded-tile" />
              ))}
            </div>
          ) : milestones.status === "error" ? (
            <ErrorState message={milestones.error.message} />
          ) : milestones.data.length === 0 ? (
            <p className="text-sm text-muted">No milestones logged for this project yet.</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {sortByDueDate(milestones.data).map((m) => (
                <li key={m.id} className="flex items-center gap-2 rounded-tile bg-ground px-3 py-2">
                  <div className="flex min-w-0 flex-col leading-tight">
                    <span className="truncate text-sm font-semibold text-ink">{m.title}</span>
                    <span className="truncate text-xs text-muted tnum">
                      {formatDate(m.due_date)} · {relativeDue(m.due_date, today())}
                    </span>
                  </div>
                  <span className="ml-auto shrink-0">
                    <MilestoneStatusPill status={m.status} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Dialog>
  );
}
