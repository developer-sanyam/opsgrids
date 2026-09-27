import { useRef, useState } from "react";

import { ApiError, problemMessage, problemSlug, useAuth, useMutation } from "@opsgrids/client";
import { Button, Card, InlineAlert, TextField, useToast } from "@opsgrids/ui";

import { changePassword, completeSessionAndReload, switchOrganization } from "@/api/auth";
import { PageHeader } from "@/ui";

import { MfaSection } from "./MfaSection";

/** Change password, set up two-factor, and — when there is more than one — switch organisation. */
export function SecurityPage() {
  const { user } = useAuth();
  // A session an identity provider opened carries `sso` and never `pwd` (`SSO_AMR` in auth-service).
  const singleSignOn = user !== null && user.amr.includes("sso") && !user.amr.includes("pwd");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Account" title="Security" description="Your own sign-in — not your organisation's settings." />
      <PasswordSection />
      <MfaSection enabled={user?.mfa_enabled ?? false} singleSignOn={singleSignOn} />
      {user && user.organizations.length > 1 ? <OrgSwitchSection organizations={user.organizations} currentId={user.organization_id} /> : null}
    </div>
  );
}

function PasswordSection() {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [confirmError, setConfirmError] = useState<string | undefined>(undefined);
  const currentRef = useRef<HTMLInputElement>(null);

  const save = useMutation<void, void>(() => changePassword(current, next), {
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success("Password changed.");
    },
  });

  function submit() {
    setConfirmError(undefined);
    if (next !== confirm) {
      setConfirmError("Doesn't match the new password.");
      return;
    }
    void save.mutate();
  }

  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-lg font-bold tracking-tight">Change password</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        noValidate
        className="flex max-w-sm flex-col gap-4"
      >
        <TextField
          ref={currentRef}
          label="Current password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          error={save.fieldErrors.current_password}
        />
        <TextField
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          helper="At least 8 characters."
          error={save.fieldErrors.new_password}
        />
        <TextField
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          error={confirmError}
        />
        {save.error && Object.keys(save.fieldErrors).length === 0 ? (
          <InlineAlert tone="danger" title={problemMessage(save.error)} />
        ) : null}
        <Button type="submit" loading={save.pending}>
          {save.pending ? "Saving…" : "Change password"}
        </Button>
      </form>
    </Card>
  );
}

function OrgSwitchSection({
  organizations,
  currentId,
}: {
  organizations: { organization_id: string; name: string }[];
  currentId: string | null;
}) {
  const switchMutation = useMutation((organizationId: string) => switchOrganization(organizationId), {
    onSuccess: (result) => {
      if (result.status === "complete") completeSessionAndReload(result.access_token, "/");
    },
  });
  // The token is already for that organisation: this list is out of date (another tab switched).
  const alreadyThere =
    switchMutation.error instanceof ApiError && problemSlug(switchMutation.error.problem) === "already-in-organization";

  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-lg font-bold tracking-tight">Switch organisation</h2>
      <ul className="flex list-none flex-col gap-2 p-0">
        {organizations.map((org) => (
          <li key={org.organization_id} className="flex items-center justify-between gap-3 rounded-tile border border-line px-4 py-3">
            <span className="text-sm font-semibold text-ink">{org.name}</span>
            {org.organization_id === currentId ? (
              <span className="text-xs font-semibold uppercase tracking-wide text-faint">Current</span>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                loading={switchMutation.pending}
                onClick={() => void switchMutation.mutate(org.organization_id)}
              >
                Switch
              </Button>
            )}
          </li>
        ))}
      </ul>
      {alreadyThere ? (
        <InlineAlert
          tone="info"
          title="You're already in that organisation."
          action={
            <Button type="button" size="sm" variant="secondary" onClick={() => window.location.reload()}>
              Reload
            </Button>
          }
        >
          This list is out of date. Reload the page to see where you are now.
        </InlineAlert>
      ) : switchMutation.error ? (
        <InlineAlert tone="danger" title={problemMessage(switchMutation.error)} />
      ) : null}
    </Card>
  );
}
