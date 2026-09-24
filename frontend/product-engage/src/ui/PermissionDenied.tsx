import { Card, EmptyState, PageHeader, ShieldIcon } from "@opsgrids/ui";

/**
 * What a screen shows when the caller is known not to hold the permission it needs.
 *
 * It is a state, not an error: nothing failed, and a role is not something this screen can fix.
 * It is also **presentation, not protection** — the route is reachable by URL either way and every
 * endpoint behind it refuses the call on its own, which is why the copy names the role rather than
 * claiming the data is hidden. `RequirePermission` renders this on a failed grants fetch as well as
 * on a real denial, so the sentence covers both without asserting which one it is.
 *
 * It is a whole page, so it carries the page's `h1` — the screen it stands in for would have had
 * one, and a page with none leaves a screen-reader user's heading navigation with nothing to land
 * on. `EmptyState`'s own title is a paragraph, so the heading is the screen's name and the
 * paragraph says what is wrong.
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
          description={`Either your role doesn't include seeing ${what}, or your access couldn't be checked. Reload the page; if it persists, someone who manages roles in your organization can add the permission.`}
        />
      </Card>
    </div>
  );
}
