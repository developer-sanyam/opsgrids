import { Link } from "react-router-dom";

import {
  Card,
  ClockIcon,
  CoinsIcon,
  EmptyState,
  ErrorState,
  EyeIcon,
  PauseCircleIcon,
  RefreshIcon,
  ShieldIcon,
  TableSkeleton,
  Timeline,
} from "@opsgrids/ui";
import type { TimelineEntry, TimelineTone } from "@opsgrids/ui";

import type { AuditOut, OrgStateOut } from "@/api/types";
import type { AsyncState } from "@/lib/useAsync";
import { formatDateTime, relativeDays } from "@/lib/format";

/* --------------------------- recent account activity -------------------------- */

interface RecentActivityCardProps {
  /** `null` (once loaded) means the operator holds neither `owner` nor `support` — the console
   *  never called `/admin/audit`, which those two roles alone may read. */
  audit: AsyncState<AuditOut[] | null>;
  orgNameById: Map<string, string>;
  canSee: boolean;
}

const ACTIVITY_TONE: Record<string, TimelineTone> = {
  suspend: "negative",
  reactivate: "positive",
  comp_grant: "accent",
  "organization.read": "neutral",
  "organization.members.read": "neutral",
};

function activityIcon(action: string) {
  if (action === "suspend") return <PauseCircleIcon className="size-4" />;
  if (action === "reactivate") return <RefreshIcon className="size-4" />;
  if (action === "comp_grant") return <CoinsIcon className="size-4" />;
  return <EyeIcon className="size-4" />;
}

/** A plain-language sentence for one operator audit row. `/admin/audit` records only what an
 *  operator did (a suspend, a reactivate, a comp grant) or read — there is no cross-product
 *  activity feed of a tenant's *own* actions (no such event stream exists yet), so this never
 *  invents "plan upgraded" or "new user invited" copy the API cannot back. */
function activitySentence(row: AuditOut, orgName: string): string {
  switch (row.action) {
    case "suspend":
      return `${orgName} suspended`;
    case "reactivate":
      return `${orgName} reactivated`;
    case "comp_grant":
      return `Credit granted to ${orgName}`;
    case "organization.read":
      return `${orgName} account viewed`;
    case "organization.members.read":
      return `${orgName} members viewed`;
    default:
      return `${orgName} — ${row.action.replace(/[._]/g, " ")}`;
  }
}

export function RecentActivityCard({ audit, orgNameById, canSee }: RecentActivityCardProps) {
  const rows = audit.status === "success" ? (audit.data ?? []) : [];

  const entries: TimelineEntry[] = rows.map((row) => {
    const orgName = orgNameById.get(row.organization_id) ?? row.organization_id;
    return {
      id: row.id,
      tone: ACTIVITY_TONE[row.action] ?? "neutral",
      content: (
        <span className="flex items-center gap-2">
          <span aria-hidden className="text-faint">
            {activityIcon(row.action)}
          </span>
          {activitySentence(row, orgName)}
        </span>
      ),
      timestamp: relativeDays(row.at),
      dateTime: row.at,
    };
  });

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6 xl:col-span-2">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold text-ink">Recent Account Activity</h2>
        <Link to="/audit" className="text-sm font-semibold text-accent hover:text-accent-strong">
          View all
        </Link>
      </div>

      {!canSee ? (
        <EmptyState
          icon={<ShieldIcon className="size-6" />}
          title="Restricted"
          description="Recent activity needs the owner or support role."
        />
      ) : audit.status === "loading" ? (
        <TableSkeleton rows={5} columns={1} label="Loading recent account activity" />
      ) : audit.status === "error" ? (
        <ErrorState message={audit.error.message} />
      ) : entries.length === 0 ? (
        <EmptyState
          title="No activity yet"
          description="Operator overrides and account reads will show up here as they happen."
        />
      ) : (
        <Timeline entries={entries} collapseAfter={8} />
      )}
    </Card>
  );
}

/* ------------------------------ trials ending soon ----------------------------- */

interface TrialsEndingCardProps {
  orgs: AsyncState<OrgStateOut[]>;
  onOpen: (org: OrgStateOut) => void;
}

/** Every trial account, soonest-ending first — real, from `trial_ends_at` on the same
 *  `/admin/organizations` payload every operator role already reads. Replaces the mockup's
 *  "Pending Approvals" (there is no approval step: signup is self-serve) and "System Health"
 *  (no health-check backend exists) — dropped per the owner's "map or drop" call, rebalanced here
 *  so the bottom row isn't left looking half-built. */
export function TrialsEndingCard({ orgs, onOpen }: TrialsEndingCardProps) {
  const rows = orgs.status === "success" ? orgs.data : [];
  const trials = rows
    .filter((org): org is OrgStateOut & { trial_ends_at: string } => org.trial_ends_at !== null)
    .sort((a, b) => a.trial_ends_at.localeCompare(b.trial_ends_at))
    .slice(0, 8);

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <h2 className="text-base font-bold text-ink">Trials ending soon</h2>

      {orgs.status === "loading" ? (
        <TableSkeleton rows={4} columns={1} label="Loading trials ending soon" />
      ) : orgs.status === "error" ? (
        <ErrorState message={orgs.error.message} />
      ) : trials.length === 0 ? (
        <EmptyState
          icon={<ClockIcon className="size-6" />}
          title="No trials ending soon"
          description="Accounts on a trial will show up here as their end date approaches."
        />
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {trials.map((org) => (
            <li key={org.organization_id}>
              <button
                type="button"
                onClick={() => onOpen(org)}
                className="flex w-full items-center justify-between gap-3 py-3 text-left transition-colors hover:bg-ground/60"
              >
                <span className="min-w-0 truncate text-sm font-medium text-ink">
                  {org.name ?? org.slug ?? org.organization_id}
                </span>
                <span className="tnum shrink-0 text-xs font-semibold text-late" title={formatDateTime(org.trial_ends_at)}>
                  Ends {relativeDays(org.trial_ends_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
