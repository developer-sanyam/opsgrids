import { ownerById } from "@/data/lookups";
import { Avatar, cn } from "@atmora/ui";

interface OwnerBadgeProps {
  ownerId: string;
  /** Hide the name and show only the avatar — for tight cells like kanban cards. */
  compact?: boolean;
  className?: string;
}

/** A deal/company/contact owner: an initials avatar and, unless compact, their name. */
export function OwnerBadge({ ownerId, compact = false, className }: OwnerBadgeProps) {
  const owner = ownerById(ownerId);
  const name = owner?.full_name ?? "Unassigned";
  if (compact) {
    return <Avatar name={name} size="sm" {...(className ? { className } : {})} />;
  }
  return (
    <span className={cn("inline-flex items-center gap-2 min-w-0", className)}>
      <Avatar name={name} size="sm" />
      <span className="truncate text-sm font-medium text-ink-soft">{name}</span>
    </span>
  );
}
