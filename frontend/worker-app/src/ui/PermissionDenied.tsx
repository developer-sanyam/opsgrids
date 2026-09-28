import { Card, EmptyState, PageHeader, ShieldIcon } from "@opsgrids/ui";

/**
 * What a screen shows when the caller is known not to hold the permission it needs. Copied from
 * `product-engage/src/ui/PermissionDenied.tsx` — same reasoning, same wording pattern.
 *
 * It is a state, not an error: nothing failed, and a role is not something this screen can fix.
 * It is also **presentation, not protection** — the route stays reachable by URL and the gateway
 * refuses the call on its own regardless, which is why the copy names the role rather than
 * claiming the data is hidden.
 */
export function PermissionDenied({ what }: { what: string }) {
  const name = `${what.charAt(0).toUpperCase()}${what.slice(1)}`;
  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title={name} />
      <Card className="p-6">
        <EmptyState
          icon={<ShieldIcon className="size-6" />}
          title={`You can't open ${what} right now`}
          description={`Either your role doesn't include seeing ${what}, or your access couldn't be checked. Reload the page; if it persists, ask your supervisor to add the permission.`}
        />
      </Card>
    </div>
  );
}
