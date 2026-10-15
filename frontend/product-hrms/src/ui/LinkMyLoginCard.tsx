import { useContext, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { AuthContext, problemMessage, refreshAccessToken, useCan, useMutation } from "@opsgrids/client";
import { Button, Card, EmptyState, InlineAlert } from "@opsgrids/ui";

import { createEmployee } from "@/api/client";
import { setMemberEmployee } from "@/api/members";

/**
 * What a login with no employee record sees where its own attendance or leave would be.
 *
 * Who it is for is decided by what they may do, and the server decides again on every call:
 *
 * * an owner or administrator (`core.identity.membership.write` at ORG, plus
 *   `hrms.people.employee.write` at ORG) gets **Create my employee record** -- an employee named
 *   as the login is, then the link, in that order. The two calls are not one transaction: employee
 *   and account are separate services and the link is account's alone to write (ADR-0008 §1). If the
 *   link fails after the record was made, the next tap reuses that record instead of making a second;
 *   if the page is left in between, the record stays and is linked from its page under Employees.
 * * someone who may link but not create is pointed at Employees, where "Linked member" picks an
 *   existing record.
 * * everyone else is told who to ask.
 *
 * A first link takes effect at the next token refresh (ADR-0008 §7 bumps nothing for it), so the
 * success path asks for one before telling the page to look again.
 */
export function LinkMyLoginCard({
  title = "No employee record linked to your account",
  onLinked,
}: {
  title?: string;
  /** Called once the link exists and the session has been refreshed. */
  onLinked: () => void;
}) {
  const me = useContext(AuthContext)?.user ?? null;
  const canLink = useCan("core.identity.membership.write", "ORG");
  const canCreate = useCan("hrms.people.employee.write", "ORG");
  // The employee made by a first attempt whose link then failed: reused, never made twice.
  const created = useRef<string | null>(null);
  const [refreshNote, setRefreshNote] = useState<string | undefined>(undefined);

  const run = useMutation(
    async () => {
      if (me === null || me.membership_id === null) {
        throw new Error("This session has no membership to link.");
      }
      if (created.current === null) {
        const employee = await createEmployee({ full_name: me.full_name.trim() || me.email });
        created.current = employee.id;
      }
      await setMemberEmployee(me.membership_id, created.current);
      return created.current;
    },
    {
      onSuccess: async () => {
        const result = await refreshAccessToken();
        if (result.outcome !== "ok") {
          setRefreshNote("Linked. Sign out and back in once so this page can see it.");
          return;
        }
        onLinked();
      },
    },
  );

  const body = canLink && canCreate && me !== null && me.membership_id !== null && me.full_name.trim() !== "";

  return (
    <Card className="p-6">
      <EmptyState
        title={title}
        description={
          body
            ? "Your sign-in isn't tied to an employee yet. Create your employee record to link it, so your attendance and leave are yours."
            : canLink
              ? "Open the employee this is under Employees and choose Linked member."
              : "Ask an administrator to link your sign-in to your employee record."
        }
      />
      <div className="mt-4 flex flex-col items-center gap-3">
        {body ? (
          <Button loading={run.pending} onClick={() => void run.mutate(undefined)}>
            Create my employee record
          </Button>
        ) : null}
        {canLink && !body ? (
          <Link to="/employees" className="text-sm font-semibold text-accent hover:text-accent-strong">
            Go to Employees
          </Link>
        ) : null}
        {run.error ? (
          <InlineAlert tone="danger" title="Couldn't link your account">
            {problemMessage(run.error)}
            {created.current !== null
              ? " Your employee record was created; try again to finish linking it."
              : ""}
          </InlineAlert>
        ) : null}
        {refreshNote ? (
          <InlineAlert tone="info" live="off" title="Linked">
            {refreshNote}
          </InlineAlert>
        ) : null}
      </div>
    </Card>
  );
}
