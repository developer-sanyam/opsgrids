import { useState } from "react";
import type { Ref } from "react";

import {
  AlertTriangleIcon,
  ArrowRightIcon,
  ArrowUpRight,
  CheckIcon,
  ClockIcon,
  MessageSquareIcon,
  ShieldIcon,
} from "@opsgrids/ui";

import type { SignupConfig } from "@/api/whatsappSignup";
import { Button, EmptyState, InlineAlert, LogoMark } from "@/ui";
import { LinkButton, StepCard } from "./parts";
import type { MetaSignup } from "./useMetaSignup";

const NEEDS = [
  "A Facebook login that manages your business.",
  "A phone number that can get a code by SMS or call, ideally one you can dedicate to WhatsApp automation.",
  "Your business name and website, for Meta to review your display name.",
  "A payment method for Meta’s message charges.",
  "Customer opt-in. You may only message people who agreed to hear from you.",
];

const AUTOMATIC = [
  "Saves your access token, encrypted.",
  "Subscribes to message and delivery events.",
  "Syncs your message templates.",
];

/**
 * Step 1: what the customer needs, and the one button that opens Meta.
 *
 * When the server has no Meta app configured (`signup/config.enabled` is false) this is a
 * not-configured state instead — the button is not rendered disabled, it is not rendered, because
 * a control that can never work is a broken button with a label on it. The manual token form that
 * already exists is offered as the way through.
 */
export function ConnectStep({
  config,
  signup,
  onContinue,
  headingRef,
}: {
  config: SignupConfig;
  signup: MetaSignup;
  onContinue: () => void;
  headingRef: Ref<HTMLHeadingElement>;
}) {
  if (!config.enabled || config.app_id === null || config.config_id === null) {
    return (
      <StepCard title="Connecting with Meta isn't set up yet" headingRef={headingRef} centered>
        <EmptyState
          icon={<AlertTriangleIcon className="size-6" />}
          title="This OpsGrids server has no Meta app configured"
          description="Meta's sign-in window needs an app ID and an Embedded Signup configuration on the server. Someone who administers OpsGrids can add them. Until then you can connect with an access token from Meta Business Manager."
          action={<LinkButton to="/whatsapp/connection">Enter credentials manually</LinkButton>}
        />
      </StepCard>
    );
  }

  return (
    <StepCard
      title="Connect your WhatsApp Business Account"
      description="Sign in through Meta's secure window. It takes about 3 minutes, and OpsGrids handles the rest."
      headingRef={headingRef}
      centered
    >
      <div aria-hidden className="flex items-center justify-center gap-4">
        <span className="grid size-16 place-items-center rounded-card bg-positive-soft text-positive">
          <MessageSquareIcon className="size-8" />
        </span>
        <ArrowRightIcon className="size-6 text-faint" />
        <span className="grid size-16 place-items-center rounded-card bg-accent-soft">
          <LogoMark className="size-9" />
        </span>
      </div>

      <section aria-labelledby="ready-heading" className="flex flex-col gap-3 rounded-card bg-positive-soft p-4 text-left">
        <h2 id="ready-heading" className="text-body-sm font-bold text-positive">
          Have these ready
        </h2>
        <ul className="flex list-none flex-col gap-3 p-0">
          {NEEDS.map((text) => (
            <li key={text} className="flex items-start gap-3 text-body text-ink">
              <CheckIcon aria-hidden className="mt-1 size-4 shrink-0 text-positive" />
              {text}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="auto-heading" className="flex flex-col gap-3 text-left">
        <h2 id="auto-heading" className="text-body-sm font-bold text-ink-soft">
          OpsGrids then does this for you
        </h2>
        <ul className="flex list-none flex-col gap-2 p-0">
          {AUTOMATIC.map((text) => (
            <li key={text} className="flex items-center gap-3 text-body-sm text-muted">
              <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-faint" />
              {text}
            </li>
          ))}
        </ul>
      </section>

      <div className="flex w-full flex-col gap-3">
        <Button
          onClick={onContinue}
          disabled={signup.sdk !== "ready"}
          trailingIcon={<ArrowUpRight className="size-4" aria-hidden />}
          className="w-full"
        >
          Continue with Meta
        </Button>
        <div role="status" aria-live="polite" className="empty:hidden">
          {signup.sdk === "loading" ? (
            <p className="text-body-sm text-muted">Loading Meta&apos;s sign-in…</p>
          ) : null}
        </div>
        {signup.sdk === "failed" ? (
          <InlineAlert
            tone="danger"
            title="Meta's sign-in couldn't be loaded"
            action={
              <Button variant="outline" size="sm" onClick={signup.retryLoad}>
                Try again
              </Button>
            }
          >
            Check that an ad blocker or firewall isn&apos;t blocking connect.facebook.net, then try again.
          </InlineAlert>
        ) : null}
      </div>

      <div className="flex flex-col items-center gap-2 text-center">
        <p className="text-body-sm text-muted">
          You will be sent to Meta to sign in. OpsGrids never sees your Facebook password.
        </p>
        <p className="inline-flex items-center gap-2 text-caption text-muted">
          <ShieldIcon aria-hidden className="size-3.5" />
          Only an Owner or Admin can connect WhatsApp.
        </p>
      </div>
    </StepCard>
  );
}

const ASKS = [
  { title: "Log in to Facebook", text: "Use the account that manages your business." },
  { title: "Choose your business portfolio", text: "Pick an existing one or create a new one." },
  {
    title: "Choose or create a WhatsApp Business Account",
    text: "It holds your numbers and message templates.",
  },
  { title: "Add your phone number", text: "Meta sends a code by SMS or call to verify it." },
];

/**
 * Step 1, second half: the Meta window is open and this page waits. The waiting message is a
 * polite live region so the outcome — cancelled, blocked, error, on its way — is announced
 * without the customer having to look for it.
 */
export function MetaStep({
  signup,
  onReopen,
  onBack,
  headingRef,
}: {
  signup: MetaSignup;
  onReopen: () => void;
  onBack: () => void;
  headingRef: Ref<HTMLHeadingElement>;
}) {
  const [unconfirmed, setUnconfirmed] = useState(false);
  const { phase } = signup;
  const working = phase === "completing";

  return (
    <StepCard
      title="Finish in the Meta window"
      description="We opened Meta in a new window. Complete these steps there, then come back. This page updates by itself."
      headingRef={headingRef}
      centered
    >
      <ol className="flex w-full list-none flex-col overflow-hidden rounded-card p-0 text-left shadow-card">
        {ASKS.map((ask, index) => (
          <li key={ask.title} className="flex items-start gap-3 border-t border-line p-4 first:border-t-0">
            <span
              aria-hidden
              className="grid size-7 shrink-0 place-items-center rounded-full bg-sunken text-body-sm font-bold text-ink-soft tnum"
            >
              {index + 1}
            </span>
            <span className="flex flex-col gap-1">
              <span className="text-body font-bold text-ink">{ask.title}</span>
              <span className="text-body-sm text-muted">{ask.text}</span>
            </span>
          </li>
        ))}
      </ol>

      <div role="status" aria-live="polite" className="w-full text-left">
        {phase === "waiting" ? (
          <InlineAlert tone="info" live="off" title="Waiting for Meta. Nothing needs to be done on this page." />
        ) : null}
        {phase === "idle" ? (
          <InlineAlert tone="warning" live="off" title="The Meta window isn't open">
            Choose Reopen Meta window to start the sign-in.
          </InlineAlert>
        ) : null}
        {working ? (
          <InlineAlert tone="info" live="off" title="Meta confirmed. Starting setup…" />
        ) : null}
        {phase === "cancelled" ? (
          <InlineAlert tone="warning" live="off" title="The Meta window was closed before setup finished">
            Nothing was connected. Choose Reopen Meta window to start again.
          </InlineAlert>
        ) : null}
        {phase === "blocked" ? (
          <InlineAlert tone="warning" live="off" title="Your browser blocked the Meta window">
            Allow pop-ups for this site, then choose Reopen Meta window.
          </InlineAlert>
        ) : null}
        {phase === "error" ? (
          <InlineAlert tone="danger" live="off" title="Setup couldn't be started">
            {signup.message ?? "Meta reported an error."} Choose Reopen Meta window to try again.
          </InlineAlert>
        ) : null}
        {unconfirmed && phase !== "completing" ? (
          <p className="mt-3 flex items-center gap-2 text-body-sm text-ink-soft">
            <ClockIcon aria-hidden className="size-4 shrink-0" />
            Meta hasn&apos;t confirmed yet. Finish every step in the Meta window, or choose Reopen Meta window.
          </p>
        ) : null}
      </div>

      <p className="flex w-full items-start gap-3 rounded-tile bg-late-soft px-4 py-3 text-left text-body-sm text-ink">
        <AlertTriangleIcon aria-hidden className="mt-1 size-4 shrink-0 text-late" />
        <span>Window did not open? Allow pop-ups for this site, then choose Reopen Meta window.</span>
      </p>

      <div className="flex w-full flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" onClick={onBack} disabled={working}>
          Cancel and go back
        </Button>
        <div className="flex flex-wrap gap-3">
          <Button
            variant="outline"
            onClick={onReopen}
            disabled={working}
            leadingIcon={<ArrowUpRight aria-hidden className="size-4" />}
          >
            Reopen Meta window
          </Button>
          <Button
            disabled={working}
            onClick={() => setUnconfirmed(true)}
          >
            I have finished
          </Button>
        </div>
      </div>
    </StepCard>
  );
}
