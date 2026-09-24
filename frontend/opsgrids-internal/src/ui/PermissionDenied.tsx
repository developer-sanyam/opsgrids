import type { ReactNode } from "react";

import { Card, EmptyState, PageHeader, ShieldIcon } from "@opsgrids/ui";

import { useOperatorAuth } from "@/auth/context";
import { ROLE_LABEL, rolesFor } from "@/auth/roles";
import type { Capability } from "@/auth/roles";

/**
 * What a screen shows when the operator is known not to hold a role it needs.
 *
 * A state, not an error: nothing failed, and a role is not something this screen can fix. It is
 * also **presentation, not protection** — the route is reachable by URL either way and every
 * endpoint behind it refuses the call on its own (a plain 403 for an operator whose role is
 * missing), which is why the copy names the roles rather than claiming the data is hidden.
 *
 * It is a whole page, so it carries the page's `h1`: the screen it stands in for would have had
 * one, and a page with none leaves heading navigation with nothing to land on.
 */
export function PermissionDenied({ what, capability }: { what: string; capability: Capability }) {
  const name = `${what.charAt(0).toUpperCase()}${what.slice(1)}`;
  const admitted = rolesFor(capability)
    .map((role) => ROLE_LABEL[role])
    .join(", ");
  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title={name} />
      <Card className="p-6">
        <EmptyState
          icon={<ShieldIcon className="size-6" />}
          title={`You can't open ${what}`}
          description={`Your operator role doesn't include ${what}. It is open to: ${admitted}. If you need it, ask an owner.`}
        />
      </Card>
    </div>
  );
}

/**
 * The route-level guard: renders its children only for an operator whose roles admit the
 * capability, and the permission-denied state otherwise. It hides UI; the server refuses the call
 * regardless.
 */
export function RequireCapability({
  capability,
  what,
  children,
}: {
  capability: Capability;
  what: string;
  children: ReactNode;
}) {
  const { can } = useOperatorAuth();
  if (!can(capability)) return <PermissionDenied what={what} capability={capability} />;
  return <>{children}</>;
}
