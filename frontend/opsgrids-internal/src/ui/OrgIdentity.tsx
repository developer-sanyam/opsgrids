import { orgIdentity } from "@/lib/format";
import { cn } from "@/lib/cn";

interface OrgIdentityProps {
  name: string | null;
  slug: string | null;
  organizationId: string;
  className?: string;
}

/**
 * How an organization renders wherever a bulk list names one — the Accounts list, the
 * Subscriptions list and Overview's attention rows. `name`/`slug` come from account-service's
 * directory mirror and are `null` until the identity fold or the one-off backfill has reached a
 * row; that gap is real, not a loading state, so it falls back to the id itself rather than a
 * spinner or a guess.
 */
export function OrgIdentity({ name, slug, organizationId, className }: OrgIdentityProps) {
  const identity = orgIdentity({ name, slug, organization_id: organizationId });
  if (!identity.secondary && identity.primary === organizationId) {
    return <span className={cn("mono truncate text-sm font-bold text-ink", className)}>{organizationId}</span>;
  }
  return (
    <span className={cn("flex min-w-0 flex-col", className)}>
      <span className="truncate text-sm font-bold text-ink">{identity.primary}</span>
      <span className="mono truncate text-xs text-faint">{identity.secondary ?? organizationId}</span>
    </span>
  );
}
