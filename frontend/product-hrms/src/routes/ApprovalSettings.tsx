import { useCallback, useEffect, useState } from "react";

import { usePermission } from "@opsgrids/client";
import {
  Announcer,
  Card,
  ErrorState,
  InlineAlert,
  PageHeader,
  Skeleton,
  useAnnounce,
} from "@opsgrids/ui";

import { listPolicies } from "@/api/approvals";
import type { PolicyOut } from "@/api/types";
import { PolicyCard } from "@/ui/PolicyCard";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "success"; policies: PolicyOut[] };

/**
 * §4.G — slice 4 only. This screen configures a sweep that does not run before slice 4, so it
 * (and its nav entry, `@/ui/Sidebar`) do not exist any earlier.
 */
function ApprovalSettingsScreen() {
  const announce = useAnnounce();
  // §2.4 rule 2, §5 row 16: `policy.write` is floored at ORG. `usePermission` reads `false` when
  // the grants source is unknown (gap **G1**'s documented fallback) — the safe default, since
  // showing an editable form to everyone and letting `PUT` 403 is not an option. Reached by URL
  // without the grant, the screen still renders every value, read-only (§4.G's "No permission").
  const canEdit = usePermission("core.approval.policy.write", "ORG");

  const [state, setState] = useState<LoadState>({ status: "loading" });

  const load = useCallback(() => {
    setState({ status: "loading" });
    listPolicies()
      .then((policies) => setState({ status: "success", policies }))
      .catch((err: unknown) =>
        setState({
          status: "error",
          message: err instanceof Error ? err.message : "Something went wrong",
        }),
      );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Approvals"
        title="Approval settings"
        description="How long approvers get, and what happens when nobody answers."
      />

      {!canEdit ? (
        <InlineAlert tone="warning" title="You can't change approval settings" live="off">
          Only an approvals administrator can change these. You can still see the current settings
          below.
        </InlineAlert>
      ) : null}

      {state.status === "loading" ? (
        <Card className="p-6 flex flex-col gap-4">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-10 w-40" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={state.message} onRetry={load} />
        </Card>
      ) : null}

      {state.status === "success"
        ? state.policies.map((policy) => (
            <PolicyCard
              key={policy.request_type}
              policy={policy}
              canEdit={canEdit}
              announce={announce}
            />
          ))
        : null}
    </div>
  );
}

export function ApprovalSettingsPage() {
  return (
    <Announcer>
      <ApprovalSettingsScreen />
    </Announcer>
  );
}
