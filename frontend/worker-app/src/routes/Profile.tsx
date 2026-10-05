import { Avatar, Button, Card, PageHeader } from "@opsgrids/ui";
import { useAuth, useCurrentUser } from "@opsgrids/client";

import { useSignOut } from "@/lib/useSignOut";

/**
 * Who is signed in, and the way out. Needs nothing beyond what `AuthProvider` already loaded from
 * `GET /auth/me` at boot, so — unlike task/scan screens — this is real today.
 */
export function ProfilePage() {
  const { user } = useAuth();
  const currentUser = useCurrentUser();
  // `logout()` clears the local session even when the server call fails, and `RequireAuth` takes
  // the app to `/login` once `status` flips — so there is nothing for this screen to recover.
  const { requestSignOut, signingOut, dialog } = useSignOut();

  if (!currentUser || !user) return null;

  const activeOrg = user.organizations.find((org) => org.organization_id === user.organization_id);

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

      <Button variant="secondary" onClick={requestSignOut} loading={signingOut}>
        Sign out
      </Button>
      {dialog}
    </div>
  );
}
