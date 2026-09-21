import type { Project } from "@/api/types";
import { Can } from "@opsgrids/client";
import { Avatar, Button, Dialog, cn } from "@opsgrids/ui";
import { formatDate, formatMoney } from "@/lib/format";
import { ProjectStatusPill } from "@/ui";

/**
 * The drill-down for one engagement: progress, spend and dates, in a real `Dialog` — focus
 * trapped, initial focus on open, focus returned to the row that opened it on close. The
 * hand-rolled `role="dialog"` this replaced had none of that.
 */
export function ProjectDetailDialog({
  project,
  ownerName,
  onClose,
  onEdit,
  onDelete,
}: {
  project: Project;
  ownerName: string;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const overBudget = project.spent_minor > project.budget_minor;
  const remaining = project.budget_minor - project.spent_minor;
  const spentPct = project.budget_minor === 0 ? 0 : Math.round((project.spent_minor / project.budget_minor) * 100);

  return (
    <Dialog
      open
      onClose={onClose}
      title={project.name}
      description={
        <span className="inline-flex items-center gap-2">
          <span className="tnum">{project.code}</span>
          <ProjectStatusPill status={project.status} />
        </span>
      }
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Can permission="pulseboard.engagements.project.write">
            <Button variant="danger" onClick={onDelete}>
              Delete
            </Button>
            <Button onClick={onEdit}>Edit</Button>
          </Can>
        </>
      }
    >
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-sm">
            <span className="font-semibold text-ink">Progress</span>
            <span className="tnum font-semibold text-ink-soft">{project.progress_pct}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-ground">
            <div className="h-full rounded-full bg-accent" style={{ width: `${project.progress_pct}%` }} />
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded-card bg-ground p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">Spent</span>
            <span className={cn("tnum font-bold", overBudget ? "text-danger" : "text-ink")}>
              {formatMoney(project.spent_minor, project.currency)}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">Budget</span>
            <span className="tnum font-semibold text-ink-soft">
              {formatMoney(project.budget_minor, project.currency)}
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-surface">
            <div
              className={cn("h-full rounded-full", overBudget ? "bg-danger" : "bg-accent")}
              style={{ width: `${Math.min(spentPct, 100)}%` }}
            />
          </div>
          <div className="flex items-center justify-between border-t border-line pt-3 text-sm">
            <span className="text-muted">{overBudget ? "Over budget by" : "Remaining"}</span>
            <span className={cn("tnum font-bold", overBudget ? "text-danger" : "text-present")}>
              {formatMoney(Math.abs(remaining), project.currency)}
            </span>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-4 text-sm">
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted">Client</dt>
            <dd className="font-semibold text-ink">{project.client || "—"}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted">Owner</dt>
            <dd className="flex items-center gap-2 font-semibold text-ink">
              <Avatar name={ownerName} size="xs" />
              {ownerName}
            </dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted">Started</dt>
            <dd className="tnum font-semibold text-ink">{formatDate(project.start_date)}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted">Due</dt>
            <dd className="tnum font-semibold text-ink">{formatDate(project.due_date)}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted">Open tasks</dt>
            <dd className="tnum font-semibold text-ink">{project.open_tasks}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted">Billing</dt>
            <dd className="font-semibold text-ink">{project.billable ? "Billable" : "Internal"}</dd>
          </div>
        </dl>
      </div>
    </Dialog>
  );
}
