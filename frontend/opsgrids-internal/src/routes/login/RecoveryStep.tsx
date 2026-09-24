import { useEffect, useRef, useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import { Button, InlineAlert, Switch } from "@opsgrids/ui";

import { useOperatorAuth } from "@/auth/context";
import { referenceOf } from "@/lib/errors";
import { SignInCard } from "./SignInCard";

/**
 * The recovery codes, shown once. They are stored hashed and cannot be shown again, and each one
 * signs in once if the authenticator is lost — so this is the only time they can be saved.
 *
 * The session is already minted by then, but it is not live until the operator confirms they have
 * saved the codes: the redirect that follows a live session would otherwise take this screen away
 * before anyone could read it. Focus lands on the heading, which says what this is; the codes are
 * plain text in a list so they can be selected, and nothing here writes them anywhere.
 */
export function RecoveryStep({ recoveryCodes }: { recoveryCodes: string[] }) {
  const { finishEnrolment } = useOperatorAuth();
  const [saved, setSaved] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const finish = useMutation<void, void>(() => finishEnrolment());

  return (
    <SignInCard
      title="Save your recovery codes"
      titleRef={headingRef}
      intro="Each code signs you in once if you lose access to your authenticator app."
    >
      <div className="flex flex-col gap-4">
        <InlineAlert tone="warning" title="Shown only once — save these somewhere safe." live="off" />

        <ul className="grid grid-cols-1 gap-2 rounded-tile bg-ground p-4 text-sm text-ink sm:grid-cols-2">
          {recoveryCodes.map((code) => (
            <li key={code} className="money select-all">
              {code}
            </li>
          ))}
        </ul>

        {finish.error ? (
          <InlineAlert
            tone="danger"
            title={`Couldn't finish signing in. ${problemMessage(finish.error)}`}
            reference={referenceOf(finish.error)}
          />
        ) : null}

        <Switch label="I've saved these codes" checked={saved} onChange={setSaved} />

        <Button loading={finish.pending} disabled={!saved} onClick={() => void finish.mutate()}>
          {finish.pending ? "Signing in…" : "Continue to the console"}
        </Button>
      </div>
    </SignInCard>
  );
}
