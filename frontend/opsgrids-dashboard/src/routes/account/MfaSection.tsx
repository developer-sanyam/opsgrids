import { useState } from "react";

import { Button, Card, useToast } from "@opsgrids/ui";

import type { MfaEnrolled, MfaEnrolStart } from "@/api/auth";

import { MfaConfirmForm } from "./MfaConfirmForm";
import { MfaStartForm } from "./MfaStartForm";
import { RecoveryCodesCard } from "./RecoveryCodesCard";

interface MfaSectionProps {
  /** The account has a confirmed authenticator (`me.mfa_enabled`). */
  enabled: boolean;
  /** This session was opened through single sign-on (`me.amr` says `sso` and not `pwd`). */
  singleSignOn: boolean;
}

/** Where the person is in adding an authenticator. `idle` is the card that says where things stand. */
type Step =
  | { kind: "idle" }
  | { kind: "password" }
  | { kind: "scan"; enrolment: MfaEnrolStart }
  | { kind: "recovery"; codes: string[] };

const COPY = {
  title: "Two-factor authentication",
  enabled: "Enabled for your account.",
  notSetUp: "Not set up yet. Adding it means a code from your phone alongside your password.",
  setUp: "Set up two-factor authentication",
  addAnother: "Add another authenticator",
  added: "Authenticator added.",
  enabledToast: "Two-factor authentication enabled.",
  sso: {
    notSetUp:
      "Not available with single sign-on. Your organisation's identity provider checks who you are when you sign in this way, so an authenticator app can't be added to this session. If you have a password, sign in with it to set one up.",
    enabled: "Adding another authenticator isn't available with single sign-on. Sign in with your password to add one.",
  },
};

/**
 * Two-factor authentication on the Security page: where it stands, and adding an authenticator.
 *
 * Adding one is two steps behind a re-check of who is asking — the password (and a code from the
 * authenticator already there, when there is one), then a code from the new one. Recovery codes
 * exist only for an account's **first** authenticator; confirming a second returns none, and no
 * "save these" card is shown for it.
 *
 * A single sign-on session cannot add one at all, and says so instead of offering a form that can
 * never succeed — whether the page already knew (`singleSignOn`) or the server said so.
 */
export function MfaSection({ enabled, singleSignOn }: MfaSectionProps) {
  const toast = useToast();
  const [step, setStep] = useState<Step>({ kind: "idle" });
  // What this visit added, on top of what the page was loaded knowing.
  const [addedHere, setAddedHere] = useState(false);
  const [refusedForSso, setRefusedForSso] = useState(false);

  const hasAuthenticator = enabled || addedHere;
  const sso = singleSignOn || refusedForSso;

  function confirmed(result: MfaEnrolled) {
    setAddedHere(true);
    if (result.recovery_codes.length > 0) {
      setStep({ kind: "recovery", codes: result.recovery_codes });
      toast.success(COPY.enabledToast);
      return;
    }
    setStep({ kind: "idle" });
    toast.success(COPY.added);
  }

  switch (step.kind) {
    case "recovery":
      return <RecoveryCodesCard codes={step.codes} onDone={() => setStep({ kind: "idle" })} />;
    case "scan":
      return (
        <MfaConfirmForm
          enrolment={step.enrolment}
          onConfirmed={confirmed}
          onStartAgain={() => setStep({ kind: "password" })}
          onCancel={() => setStep({ kind: "idle" })}
        />
      );
    case "password":
      return (
        <MfaStartForm
          adding={hasAuthenticator}
          onStarted={(enrolment) => setStep({ kind: "scan", enrolment })}
          onSingleSignOn={() => {
            setRefusedForSso(true);
            setStep({ kind: "idle" });
          }}
          onCancel={() => setStep({ kind: "idle" })}
        />
      );
    case "idle":
      break;
  }

  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-lg font-bold tracking-tight">{COPY.title}</h2>
      {hasAuthenticator ? <p className="text-sm text-muted">{COPY.enabled}</p> : null}
      {sso ? (
        <p className="text-sm text-muted">{hasAuthenticator ? COPY.sso.enabled : COPY.sso.notSetUp}</p>
      ) : (
        <>
          {hasAuthenticator ? null : <p className="text-sm text-muted">{COPY.notSetUp}</p>}
          <Button type="button" variant={hasAuthenticator ? "secondary" : "primary"} onClick={() => setStep({ kind: "password" })} className="self-start">
            {hasAuthenticator ? COPY.addAnother : COPY.setUp}
          </Button>
        </>
      )}
    </Card>
  );
}
