import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { Activity, ActivityType } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { companyName, contactName, ownerName } from "@/data/lookups";
import { CalendarIcon, CheckSquareIcon, MailIcon, NoteIcon, PhoneIcon, cn } from "@opsgrids/ui";
import { formatRelative } from "@/lib/format";

const TYPE_META: Record<
  ActivityType,
  { label: string; icon: (p: { className?: string }) => ReactNode; tone: string }
> = {
  call: { label: "Call", icon: PhoneIcon, tone: "bg-info-soft text-info" },
  email: { label: "Email", icon: MailIcon, tone: "bg-accent-soft text-accent-ink" },
  meeting: { label: "Meeting", icon: CalendarIcon, tone: "bg-cyan-soft text-cyan" },
  note: { label: "Note", icon: NoteIcon, tone: "bg-late-soft text-late" },
  task: { label: "Task", icon: CheckSquareIcon, tone: "bg-present-soft text-present" },
};

interface ActivityTimelineProps {
  activities: Activity[];
  /** Show the company link on each row (omit on a screen already scoped to one company/deal). */
  showCompany?: boolean;
  /** Show which contact the touch was with. */
  showContact?: boolean;
}

/**
 * A vertical timeline of logged touches. Each row carries a typed icon, the subject and body, the
 * owner and (optionally) cross-links to the company/contact, and a relative timestamp. A connecting
 * rail threads the icons so the sequence reads top-to-bottom.
 */
export function ActivityTimeline({
  activities,
  showCompany = true,
  showContact = true,
}: ActivityTimelineProps) {
  const { owners, companies, contacts } = useDirectory();
  return (
    <ol className="flex flex-col">
      {activities.map((activity, i) => {
        const meta = TYPE_META[activity.type];
        const Icon = meta.icon;
        const last = i === activities.length - 1;
        return (
          <li key={activity.id} className="relative flex gap-3.5 pb-5 last:pb-0">
            {!last ? (
              <span
                className="absolute left-[19px] top-10 bottom-0 w-px bg-line"
                aria-hidden
              />
            ) : null}
            <span
              className={cn(
                "relative z-10 grid size-10 shrink-0 place-items-center rounded-full",
                meta.tone,
              )}
            >
              <Icon className="size-5" />
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-1 pt-0.5">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-sm font-bold text-ink">{activity.subject}</span>
                <span className="text-xs font-medium text-faint">· {meta.label}</span>
                {activity.direction ? (
                  <span className="text-xs font-medium capitalize text-faint">
                    · {activity.direction}
                  </span>
                ) : null}
              </div>
              {activity.body ? (
                <p className="text-sm leading-snug text-muted">{activity.body}</p>
              ) : null}
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-faint">
                <span className="font-medium text-muted">{ownerName(owners, activity.owner_id)}</span>
                {showContact && activity.contact_id ? (
                  <span>· with {contactName(contacts, activity.contact_id)}</span>
                ) : null}
                {showCompany && activity.company_id ? (
                  <>
                    <span aria-hidden>·</span>
                    <Link
                      to={`/companies/${activity.company_id}`}
                      className="relative hit-44 font-medium text-accent hover:text-accent-strong transition-colors"
                    >
                      {companyName(companies, activity.company_id)}
                    </Link>
                  </>
                ) : null}
                <span aria-hidden>·</span>
                <time dateTime={activity.occurred_at} className="tnum">
                  {formatRelative(activity.occurred_at)}
                </time>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
