/**
 * The "Linked member" field on the employee page (ADR-0008 Q1, NEW-5).
 *
 * `account.memberships.employee_id` is the sole origin of the `sref` claim every `OWN`-scoped check
 * in the estate resolves against, and until this field existed nothing in `frontend/` could write
 * it. HR is the person who knows which employee record a new joiner is, so this is where the link
 * is made: pick a member who is not linked yet, press **Link member**. Two calls, both to account's
 * `PATCH /api/v1/members/{id}` with the signed-in user's own token, and no new endpoint.
 *
 * **The field renders only when `core.identity.membership.write` resolves at ORG** — the same floor
 * the server's route carries. That is a hint: someone who lacks it never sees a control that would
 * answer 403. The server is the control, so every call below still handles its own refusal.
 *
 * Four states, none of them an afterthought: *loading* (a skeleton the size of the resolved row, so
 * nothing shifts), *unlinked* (the picker, and what being unlinked costs the person), *linked* (who,
 * with the email **masked**, and an Unlink action) and *error* (what failed, and a retry).
 *
 * **Unlinking is the expensive direction** and says so before it asks: it bumps the organization's
 * permission version, so every signed-in person in the tenant refreshes their session (§7). Linking a
 * member for the first time does not — their next token simply carries the link.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, problemMessage, useCan, useMutation, usePermissions } from "@opsgrids/client";
import { Avatar, Button, Card, ConfirmDialog, ErrorState, InlineAlert, SectionHeader, Skeleton, useToast } from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

import { getMemberLinkedTo, linkMember, unlinkMember } from "@/api/members";
import type { EmployeeOut, MemberOut } from "@/api/types";
import { maskEmail } from "@/lib/mask";
import { MemberPicker } from "@/ui/MemberPicker";

type LinkState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "unlinked" }
  | { kind: "linked"; member: MemberOut };

function memberName(member: MemberOut): string {
  return member.display_name ?? member.full_name;
}

function readFailure(err: unknown): string {
  if (err instanceof ApiError && err.status === 403) {
    return "You don't have access to the member directory, so the linked member can't be shown.";
  }
  return "Couldn't load the linked member.";
}

/** The server refuses to let anybody change their *own* employee link — a 403 with this code. */
const OWN_LINK_REFUSAL = "cannot_change_own_employee_link";

function isOwnLinkRefusal(err: Error): boolean {
  return (
    err instanceof ApiError &&
    err.status === 403 &&
    (err.problem?.errors ?? []).some((e) => e.code === OWN_LINK_REFUSAL)
  );
}

/** The server's own sentence for a 409, a 404 or the own-link refusal says what to do ("That
 *  employee is already linked to another member…", "Ask another administrator…"); any other 403 is
 *  rewritten because the raw one names a permission code. */
function writeFailure(err: Error, verb: "link" | "unlink"): string {
  if (isOwnLinkRefusal(err)) return problemMessage(err);
  if (err instanceof ApiError && err.status === 403) {
    return `You don't have permission to ${verb} members.`;
  }
  return problemMessage(err);
}

/** The gate: nothing below runs — no fetch, no state — for someone the field is not for. */
export function LinkedMemberSection({ employee }: { employee: EmployeeOut }) {
  const canLink = useCan("core.identity.membership.write", "ORG");
  if (!canLink) return null;
  return <LinkedMemberCard employee={employee} />;
}

function LinkedMemberCard({ employee }: { employee: EmployeeOut }) {
  const toast = useToast();
  // The caller's own membership, from `GET /members/me/grants`. Nobody assigns their own identity:
  // the server refuses it (403), so this screen never offers the control on the caller's own row.
  const { membershipId: myMembershipId } = usePermissions();
  const [state, setState] = useState<LinkState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [picked, setPicked] = useState<ComboboxOption | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    getMemberLinkedTo(employee.id)
      .then((member) => {
        if (active) setState(member ? { kind: "linked", member } : { kind: "unlinked" });
      })
      .catch((err: unknown) => {
        if (active) setState({ kind: "error", message: readFailure(err) });
      });
    return () => {
      active = false;
    };
  }, [employee.id, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const link = useMutation((membershipId: string) => linkMember(membershipId, employee.id), {
    onSuccess: (member) => {
      setPicked(null);
      setState({ kind: "linked", member });
      toast.success(`${memberName(member)} is linked to ${employee.full_name}.`);
    },
  });

  const linkedMember = state.kind === "linked" ? state.member : null;
  const unlink = useMutation<void, MemberOut>(
    () => {
      if (!linkedMember) return Promise.reject(new Error("Nothing to unlink"));
      return unlinkMember(linkedMember.id);
    },
    {
      onSuccess: () => {
        setConfirming(false);
        setState({ kind: "unlinked" });
        toast.success(`${employee.full_name} is no longer linked to a member.`);
      },
      onError: (err) => {
        // Already gone — another tab, another administrator. The screen is now simply wrong, and
        // the honest fix is to show the truth rather than an error about a link that is not there.
        if (err instanceof ApiError && err.status === 404) {
          setConfirming(false);
          setState({ kind: "unlinked" });
        }
      },
    },
  );

  return (
    <Card className="p-6">
      <SectionHeader
        title="Linked member"
        subtitle="Who signs in as this employee. Approvals, attendance punches and “own” views in other products follow this link."
      />
      <div className="mt-4 flex flex-col gap-4">
        {state.kind === "loading" ? (
          <div aria-busy="true" aria-label="Loading the linked member">
            <Skeleton className="h-12 w-full rounded-tile" />
          </div>
        ) : null}

        {state.kind === "error" ? <ErrorState message={state.message} onRetry={retry} /> : null}

        {state.kind === "unlinked" ? (
          <>
            <p className="text-sm text-muted">
              No member is linked to {employee.full_name}. Until one is, their approvals inbox stays
              empty and they can&apos;t punch their own attendance.
            </p>
            {link.error ? (
              <InlineAlert tone="danger" title={writeFailure(link.error, "link")} />
            ) : null}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="flex-1">
                <MemberPicker
                  value={picked}
                  onChange={(option) => {
                    setPicked(option);
                    link.reset();
                  }}
                  excludeMembershipId={myMembershipId}
                  disabled={link.pending}
                />
              </div>
              <Button
                variant="dark"
                loading={link.pending}
                disabled={picked === null}
                onClick={() => {
                  if (picked) void link.mutate(picked.id);
                }}
              >
                Link member
              </Button>
            </div>
          </>
        ) : null}

        {state.kind === "linked" ? (
          <div className="flex items-center justify-between gap-3 rounded-tile bg-ground/60 px-3 py-2.5">
            <div className="flex items-center gap-3">
              <Avatar name={memberName(state.member)} size="sm" />
              <div className="flex flex-col leading-tight">
                <span className="text-sm font-semibold text-ink">
                  {memberName(state.member)}
                  {state.member.status !== "active" ? (
                    <span className="ml-2 text-xs font-normal text-muted">
                      {state.member.status}
                    </span>
                  ) : null}
                </span>
                <span className="tnum text-xs text-muted">
                  <span className="sr-only">Sign-in email, masked: </span>
                  {maskEmail(state.member.email)}
                </span>
              </div>
            </div>
            {state.member.id === myMembershipId ? null : (
              <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
                Unlink
              </Button>
            )}
          </div>
        ) : null}

        {state.kind === "linked" && state.member.id === myMembershipId ? (
          <p className="text-sm text-muted">
            This is your own membership. You can&apos;t change your own link — another administrator
            has to.
          </p>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirming}
        onClose={() => {
          setConfirming(false);
          unlink.reset();
        }}
        onConfirm={() => void unlink.mutate()}
        title={linkedMember ? `Unlink ${memberName(linkedMember)}?` : "Unlink this member?"}
        body={
          linkedMember
            ? `${memberName(linkedMember)} will stop acting as ${employee.full_name}: their approvals inbox, attendance punches and “own” records in every product go empty until they are linked again. Unlinking also refreshes everyone's sign-in in the organization, so do it sparingly in a large organization during working hours.`
            : "This member will stop acting as this employee."
        }
        confirmLabel="Unlink"
        pendingLabel="Unlinking…"
        cancelLabel="Keep the link"
        pending={unlink.pending}
        {...(unlink.error ? { error: writeFailure(unlink.error, "unlink") } : {})}
      />
    </Card>
  );
}
