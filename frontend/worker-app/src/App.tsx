import { AppShell as Shell, GrantsStatusBanner } from "@opsgrids/ui";
import { useAuth, useCurrentUser, usePermissions } from "@opsgrids/client";

import { PutawayQueueProvider } from "./lib/PutawayQueueProvider";
import { useSignOut } from "./lib/useSignOut";
import { LogoMark } from "./ui/LogoMark";
import { PutawayAttentionBanner } from "./ui/PutawayAttentionBanner";
import { PROFILE_NAV_ENTRY, useNavGroups } from "./ui/useNavGroups";

/** Once, at the shell — never per screen. Same words as `product-operations`, minus the controls it names. */
const GRANTS_BANNER_COPY = {
  title: "Permissions couldn't be checked — some actions may be hidden",
  body: "Reloading the page also retries it.",
};

/**
 * The app shell — the mobile header, bottom navigation and routed main column — is
 * `@opsgrids/ui`'s `AppShell`, the same component every product app uses. This file supplies only
 * what is this app's own: its mark, its (short) nav, the work destinations as bottom tabs
 * (`bottomNavExtra`: Counts, Put away) and Profile in the header's account menu (`accountLinks`).
 *
 * It is also where the put-away queue's drainer lives (`PutawayQueueProvider`), because a put-away
 * saved on a basement floor has to sync when the signal returns wherever the worker happens to be
 * in the app, not only while the Put away screen is open.
 *
 * No `modules` and no `adminLinks`: a picker/packer/dispatcher's device has one job (ADR-0024 §7
 * quotes ADR-0001's own reasoning — "a warehouse app and a sales app have nothing in common but
 * the account"), so cross-product switching and admin destinations are left out rather than
 * wired to unused hooks. No `search`: a handful of destinations need no command palette, and
 * `AppShell` already hides the trigger entirely when `search` is omitted.
 */
export function AppShell() {
  const { user } = useAuth();
  const { status: grantsStatus, reloadGrants } = usePermissions();
  const currentUser = useCurrentUser();
  const { groups, bottomExtra } = useNavGroups();
  const { requestSignOut, dialog } = useSignOut();

  if (!currentUser || !user) return null;

  return (
    <PutawayQueueProvider ownerSub={user.user_id} organizationId={user.organization_id}>
      <Shell
        product={{ name: "OpsGrids Worker", mark: <LogoMark className="size-7" /> }}
        nav={groups}
        user={{ name: currentUser.name, email: currentUser.email, roleLabel: currentUser.roleDisplay }}
        onSignOut={requestSignOut}
        bottomNavExtra={bottomExtra}
        accountLinks={[PROFILE_NAV_ENTRY]}
        banner={
          <>
            {grantsStatus === "error" ? (
              <GrantsStatusBanner copy={GRANTS_BANNER_COPY} onRetry={reloadGrants} />
            ) : null}
            <PutawayAttentionBanner />
          </>
        }
      />
      {dialog}
    </PutawayQueueProvider>
  );
}
