import { Avatar } from "@opsgrids/ui";

/** §2.2 — the ADR's own words: an unresolvable name is `null`, not absent, and the client renders
 *  its own fallback so the row stays operable. Never a reason to hide a row or disable an action. */
const NAME_UNAVAILABLE = "Employee record unavailable";

interface PersonRefProps {
  name: string | null;
  /** Only ever the person being *identified* — a requester, a chain rung, a candidate (C6). */
  employeeNo?: string | null;
  className?: string;
}

/**
 * The **inline** PersonRef (§2.2): a table cell, a chip, a sentence — avatar + name, no id. Used
 * for every name that is not itself the subject of the row (`approver_name`, `delegate_name`,
 * `decided_by_name`, `from_name`).
 */
export function PersonRefInline({ name, className }: Omit<PersonRefProps, "employeeNo">) {
  return (
    <span className={`inline-flex items-center gap-2 min-w-0 ${className ?? ""}`}>
      <Avatar name={name ?? NAME_UNAVAILABLE} size="xs" />
      <span className={name ? "truncate text-ink" : "truncate text-muted"}>
        {name ?? NAME_UNAVAILABLE}
      </span>
    </span>
  );
}

/**
 * The **block** PersonRef (§2.2): the requester on the inbox, `GET /requests` and the detail;
 * each chain rung; each delegate candidate — the person being identified, so `employeeNo`
 * disambiguates them. A block with no number renders name only, never a blank second line.
 */
export function PersonRefBlock({ name, employeeNo, className }: PersonRefProps) {
  // §2.2: when the name is `null`, "the title carries the employee number when one is known".
  // `Avatar` hardcodes its own `title` to the name it's given — here, the fallback string itself
  // — so it has nowhere to carry the number. This wrapper carries it instead, on the fallback
  // case only: a resolved name never needs a substitute title.
  const fallbackTitle = !name && employeeNo ? `Employee number ${employeeNo}` : undefined;
  return (
    <span
      className={`inline-flex items-center gap-3 min-w-0 ${className ?? ""}`}
      {...(fallbackTitle ? { title: fallbackTitle } : {})}
    >
      <Avatar name={name ?? NAME_UNAVAILABLE} size="sm" />
      <span className="flex min-w-0 flex-col leading-tight">
        <span className={`truncate font-semibold ${name ? "text-ink" : "text-muted"}`}>
          {name ?? NAME_UNAVAILABLE}
        </span>
        {employeeNo ? <span className="truncate text-xs text-muted tnum">{employeeNo}</span> : null}
      </span>
    </span>
  );
}
