import { useCallback, useState } from "react";

import { ApiError, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { Button, Card, ConfirmDialog, InlineAlert, SectionHeader, Select, Skeleton, useToast } from "@opsgrids/ui";

import { getMemberLinkedTo, listUnlinkedMembers, memberName, setMemberEmployee } from "@/api/members";
import type { MemberOut } from "@/api/members";
import type { EmployeeOut } from "@/api/types";

/**
 * "Linked member" -- which sign-in is this employee record (ADR-0008 Q1, built here).
 *
 * The link is what makes the platform's own-record checks mean something for a person: their
 * attendance, their leave balance, the approvals routed to them. Without it a login is just a
 * login -- "No employee record is linked to your account". Account is the only writer of the link
 * (`PATCH /members/{id}`); this card calls it with the signed-in user's own token, so the server's
 * `core.identity.membership.write` at ORG floor is what actually decides. The card renders only for
 * someone who holds it -- a hint, not the protection.
 *
 * Linking is by an administrator's explicit choice, never by matching email addresses (ADR-0000 §1:
 * a wrong link is an authorization defect, not a typo).
 */
export function LinkedMemberCard({ employee }: { employee: EmployeeOut }) {
  const canLink = useCan("core.identity.membership.write", "ORG");
  if (!canLink) return null;
  return <LinkedMemberBody employee={employee} />;
}

function LinkedMemberBody({ employee }: { employee: EmployeeOut }) {
  const toast = useToast();
  const linked = useAsync(useCallback(() => getMemberLinkedTo(employee.id), [employee.id]), [employee.id]);
  const [choice, setChoice] = useState("");
  const [confirmingUnlink, setConfirmingUnlink] = useState(false);

  const link = useMutation((membershipId: string) => setMemberEmployee(membershipId, employee.id), {
    onSuccess: (member) => {
      setChoice("");
      toast.success(`${memberName(member)} is now linked to ${employee.full_name}.`);
      linked.reload();
    },
  });
  const unlink = useMutation((membershipId: string) => setMemberEmployee(membershipId, null), {
    onSuccess: () => {
      setConfirmingUnlink(false);
      toast.success("The link was removed.");
      linked.reload();
    },
  });

  const member: MemberOut | null = linked.status === "success" ? linked.data : null;

  return (
    <Card className="p-6">
      <SectionHeader
        title="Linked member"
        subtitle={`The sign-in that is ${employee.full_name}'s own, so their attendance, leave and approvals are theirs.`}
      />
      <div className="mt-4 flex flex-col gap-3">
        {linked.status === "loading" ? <Skeleton className="h-10 w-full max-w-sm rounded-tile" /> : null}

        {linked.status === "error" ? (
          <InlineAlert tone="warning" title="Couldn't check who is linked">
            {problemMessage(linked.error)}
            <Button size="sm" variant="outline" className="mt-2" onClick={linked.reload}>
              Try again
            </Button>
          </InlineAlert>
        ) : null}

        {member !== null ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-col leading-tight">
              <span className="font-semibold text-ink">{memberName(member)}</span>
              <span className="text-xs text-muted">{member.email}</span>
            </div>
            <Button size="sm" variant="outline" onClick={() => setConfirmingUnlink(true)}>
              Unlink
            </Button>
          </div>
        ) : null}

        {linked.status === "success" && member === null ? <PickMember employee={employee} choice={choice} setChoice={setChoice} link={link} /> : null}
      </div>

      <ConfirmDialog
        open={confirmingUnlink}
        onClose={() => {
          setConfirmingUnlink(false);
          unlink.reset();
        }}
        onConfirm={() => {
          if (member !== null) void unlink.mutate(member.id);
        }}
        title="Unlink this sign-in?"
        body={`${member !== null ? memberName(member) : "This person"} will no longer be ${employee.full_name}. Their attendance, leave and approvals stop reaching them. Unlinking refreshes everyone's sign-in in the organization, so people may be signed in again once.`}
        confirmLabel="Unlink"
        pendingLabel="Unlinking…"
        pending={unlink.pending}
        {...(unlink.error ? { error: problemMessage(unlink.error) } : {})}
      />
    </Card>
  );
}

function PickMember({
  employee,
  choice,
  setChoice,
  link,
}: {
  employee: EmployeeOut;
  choice: string;
  setChoice: (value: string) => void;
  link: { mutate: (membershipId: string) => unknown; pending: boolean; error: unknown };
}) {
  const candidates = useAsync(useCallback(() => listUnlinkedMembers(), []), []);
  const options =
    candidates.status === "success"
      ? candidates.data.map((m) => ({ value: m.id, label: `${memberName(m)} (${m.email})` }))
      : [];

  if (candidates.status === "loading") return <Skeleton className="h-10 w-full max-w-sm rounded-tile" />;
  if (candidates.status === "error") {
    return (
      <InlineAlert tone="warning" title="Couldn't load the sign-ins">
        {problemMessage(candidates.error)}
      </InlineAlert>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {options.length === 0 ? (
        <p className="text-sm text-muted">
          Every sign-in in this organization is already linked to an employee. Invite the person
          first, then link them here.
        </p>
      ) : (
        <>
          <p className="text-sm text-muted">
            No sign-in is linked to {employee.full_name} yet. Choose the person this record is.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <Select
              label="Sign-in"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
              placeholder="Choose a sign-in"
              options={options}
              containerClassName="min-w-64 max-w-md flex-1"
            />
            <Button
              variant="primary"
              disabled={choice === ""}
              loading={link.pending}
              onClick={() => void link.mutate(choice)}
            >
              Link
            </Button>
          </div>
        </>
      )}
      {link.error ? (
        <InlineAlert tone="danger" title="Couldn't link them">
          {linkRefusal(link.error)}
        </InlineAlert>
      ) : null}
    </div>
  );
}

/** The server's own sentence for a refused link ("already linked to another member"), or a
 *  generic one. */
function linkRefusal(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return "Only an owner or administrator can link a sign-in to an employee.";
  }
  return problemMessage(error);
}
