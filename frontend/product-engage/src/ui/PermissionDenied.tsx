import { Card, EmptyState, ShieldIcon } from "@opsgrids/ui";

/**
 * What a screen shows when the caller is known not to hold the permission it needs.
 *
 * It is a state, not an error: nothing failed, and a role is not something this screen can fix.
 * It is also **presentation, not protection** — the route is reachable by URL either way and every
 * endpoint behind it refuses the call on its own, which is why the copy names the role rather than
 * claiming the data is hidden. `RequirePermission` renders this on a failed grants fetch as well as
 * on a real denial, so the sentence covers both without asserting which one it is.
 */
export function PermissionDenied({ what }: { what: string }) {
  return (
    <Card className="p-6">
      <EmptyState
        icon={<ShieldIcon className="size-6" />}
        title={`You can't open ${what} right now`}
        description={`Either your role doesn't include seeing ${what}, or your access couldn't be checked. Reload the page; if it persists, someone who manages roles in your organization can add the permission.`}
      />
    </Card>
  );
}
