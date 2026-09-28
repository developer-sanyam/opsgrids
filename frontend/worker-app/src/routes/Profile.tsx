import { useState } from "react";

import { Avatar, Button, Card, PageHeader } from "@opsgrids/ui";
import { useAuth, useCurrentUser } from "@opsgrids/client";

/**
 * Who is signed in, and the way out. Needs nothing beyond what `AuthProvider` already loaded from
 * `GET /auth/me` at boot, so — unlike task/scan screens — this is real today.
 */
export function ProfilePage() {
  const { user, logout } = useAuth();
  const currentUser = useCurrentUser();
  const [signingOut, setSigningOut] = useState(false);

  if (!currentUser || !user) return null;

  const activeOrg = user.organizations.find((org) => org.organization_id === user.organization_id);

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await logout();
    } catch {
      // `logout()` already clears the local session even when the server call fails
      // (`@opsgrids/client`'s `AuthProvider`) — nothing left to recover here, and `RequireAuth`
      // takes the app to `/login` regardless once `status` flips.
    }
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title="Profile" />

      <Card className="flex items-center gap-4 p-4 sm:p-6">
        <Avatar name={currentUser.name} size="lg" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="truncate text-base font-bold text-ink">{currentUser.name}</p>
          <p className="truncate text-sm text-muted">{currentUser.email}</p>
        </div>
      </Card>

      <Card className="p-0">
        <dl className="flex flex-col divide-y divide-line">
          <div className="flex items-center justify-between gap-4 px-4 py-3 sm:px-6">
            <dt className="text-sm text-muted">Role</dt>
            <dd className="text-sm font-semibold text-ink">{currentUser.roleDisplay ?? "—"}</dd>
          </div>
          {activeOrg ? (
            <div className="flex items-center justify-between gap-4 px-4 py-3 sm:px-6">
              <dt className="text-sm text-muted">Organization</dt>
              <dd className="text-sm font-semibold text-ink">{activeOrg.name}</dd>
            </div>
          ) : null}
        </dl>
      </Card>

      <Button variant="secondary" onClick={() => void handleSignOut()} loading={signingOut}>
        Sign out
      </Button>
    </div>
  );
}
