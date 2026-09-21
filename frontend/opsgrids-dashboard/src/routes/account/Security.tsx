import { useRef, useState } from "react";

import { problemMessage, useAuth, useMutation } from "@opsgrids/client";
import { Button, Card, InlineAlert, QrCode, TextField, useToast } from "@opsgrids/ui";

import {
  changePassword,
  completeSessionAndReload,
  confirmMfaEnrolment,
  startMfaEnrolment,
  switchOrganization,
} from "@/api/auth";
import type { MfaEnrolStart } from "@/api/auth";
import { PageHeader } from "@/ui";

/** Change password, set up two-factor, and — when there is more than one — switch organisation. */
export function SecurityPage() {
  const { user } = useAuth();

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Account" title="Security" description="Your own sign-in — not your organisation's settings." />
      <PasswordSection />
      <MfaSection enabled={user?.mfa_enabled ?? false} />
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

function MfaSection({ enabled }: { enabled: boolean }) {
  const toast = useToast();
  const [enrolment, setEnrolment] = useState<MfaEnrolStart | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);

  const start = useMutation<void, MfaEnrolStart>(() => startMfaEnrolment(), {
    onSuccess: (result) => setEnrolment(result),
  });

  const confirm = useMutation<void, { recovery_codes: string[] }>(
    () => confirmMfaEnrolment(enrolment?.factor_id ?? "", code),
    {
      onSuccess: (result) => {
        setRecoveryCodes(result.recovery_codes);
        toast.success("Two-factor authentication enabled.");
      },
    },
  );

  if (recoveryCodes) {
    return (
      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-lg font-bold tracking-tight">Recovery codes</h2>
        <InlineAlert tone="warning" title="Shown only once — save these somewhere safe.">
          Each one signs you in if you lose access to your authenticator app.
        </InlineAlert>
        <ul className="grid grid-cols-2 gap-2 rounded-tile bg-ground p-4 font-mono text-sm tabular-nums">
          {recoveryCodes.map((code_) => (
            <li key={code_}>{code_}</li>
          ))}
        </ul>
      </Card>
    );
  }

  if (enabled) {
    return (
      <Card className="flex flex-col gap-2 p-6">
        <h2 className="text-lg font-bold tracking-tight">Two-factor authentication</h2>
        <p className="text-sm text-muted">Enabled for your account.</p>
      </Card>
    );
  }

  if (enrolment) {
    return (
      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-lg font-bold tracking-tight">Set up two-factor authentication</h2>
        <p className="text-sm text-muted">Scan this with your authenticator app, or enter the code by hand.</p>
        <QrCode value={enrolment.otpauth_uri} label="Two-factor authentication setup code" />
        <p className="tnum break-all text-xs text-faint">{enrolment.secret}</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void confirm.mutate();
          }}
          noValidate
          className="flex max-w-xs flex-col gap-4"
        >
          <TextField
            label="6-digit code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            inputMode="numeric"
            error={confirm.fieldErrors.code}
          />
          {confirm.error && Object.keys(confirm.fieldErrors).length === 0 ? (
            <InlineAlert tone="danger" title={problemMessage(confirm.error)} />
          ) : null}
          <Button type="submit" loading={confirm.pending}>
            {confirm.pending ? "Confirming…" : "Confirm"}
          </Button>
        </form>
      </Card>
    );
  }

  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-lg font-bold tracking-tight">Two-factor authentication</h2>
      <p className="text-sm text-muted">Not set up yet. Adding it means a code from your phone alongside your password.</p>
      {start.error ? <InlineAlert tone="danger" title={problemMessage(start.error)} /> : null}
      <Button type="button" onClick={() => void start.mutate()} loading={start.pending} className="self-start">
        {start.pending ? "Starting…" : "Set up two-factor authentication"}
      </Button>
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
      {switchMutation.error ? <InlineAlert tone="danger" title={problemMessage(switchMutation.error)} /> : null}
    </Card>
  );
}
