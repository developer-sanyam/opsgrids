import { useState } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useOperatorAuth } from "@/auth/context";
import type { EnrolmentSetup } from "@/auth/context";
import type { OperatorStep } from "@/auth/types";
import { CodeStep } from "./login/CodeStep";
import { EnrolCodeStep } from "./login/EnrolCodeStep";
import { EnrolScanStep } from "./login/EnrolScanStep";
import { PasswordStep } from "./login/PasswordStep";
import { RecoveryStep } from "./login/RecoveryStep";

interface LocationState {
  from?: string;
}

/** Which screen of the sign-in is showing. The pre-auth token behind the later ones is the provider's, never this state. */
type Step =
  | { kind: "password" }
  | { kind: "mfa" }
  | { kind: "enrol_code" }
  | { kind: "enrol_scan"; setup: EnrolmentSetup }
  | { kind: "recovery"; recoveryCodes: string[] };

/**
 * The operator sign-in: password, then a second factor — never a session from the password alone.
 *
 * - An account with a confirmed authenticator goes to **the code** (`mfa_required`).
 * - An account with none goes to **enrolment** (`mfa_enrollment_required`): the out-of-band code
 *   an owner issued, then the QR code and a first code, then the recovery codes, shown once.
 *
 * A lock returns to the password step carrying the server's sentence, because the session behind
 * the later steps was revoked with it. Where the operator was headed is remembered across the
 * whole thing, and the redirect fires the moment the provider reports the session live — which,
 * after enrolment, is only once the recovery codes have been saved.
 */
export function LoginPage() {
  const { status, cancelSignIn } = useOperatorAuth();
  const location = useLocation();
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [notice, setNotice] = useState<string | undefined>(undefined);

  const destination = (location.state as LocationState | null)?.from ?? "/";

  // Already signed in (or just became so): go where the operator was headed.
  if (status === "authenticated") return <Navigate to={destination} replace />;

  function toPassword(message?: string) {
    cancelSignIn();
    setNotice(message);
    setStep({ kind: "password" });
  }

  function afterPassword(next: OperatorStep) {
    setNotice(undefined);
    setStep({ kind: next === "mfa" ? "mfa" : "enrol_code" });
  }

  switch (step.kind) {
    case "mfa":
      return <CodeStep onLocked={toPassword} onBack={() => toPassword()} />;
    case "enrol_code":
      return (
        <EnrolCodeStep
          onSetup={(setup) => setStep({ kind: "enrol_scan", setup })}
          onLocked={toPassword}
          onBack={() => toPassword()}
        />
      );
    case "enrol_scan":
      return (
        <EnrolScanStep
          setup={step.setup}
          onConfirmed={(recoveryCodes) => setStep({ kind: "recovery", recoveryCodes })}
          onLocked={toPassword}
          onBack={() => toPassword()}
        />
      );
    case "recovery":
      return <RecoveryStep recoveryCodes={step.recoveryCodes} />;
    default:
      return <PasswordStep notice={notice} onStep={afterPassword} />;
  }
}
