import { useCallback, useEffect, useRef } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { ArrowUpRight, Skeleton } from "@opsgrids/ui";

import { getSignupConfig } from "@/api/whatsappSignup";
import type { SignupCompleteOut } from "@/api/whatsappSignup";
import { RequireGrant } from "@/auth/RequireGrant";
import { Announcer, Card } from "@/ui";
import { ConnectStep, MetaStep } from "./ConnectStartMeta";
import { DoneStep } from "./DoneStep";
import { ConnectProgress, LinkButton, LoadError } from "./parts";
import type { WizardStep } from "./parts";
import { SelectStep } from "./SelectStep";
import { SetupStep } from "./SetupStep";
import { useMetaSignup } from "./useMetaSignup";

const STEPS: readonly WizardStep[] = ["start", "meta", "select", "setup", "done"];

/** A setup id is a UUID the server minted. It is the only thing the URL carries between steps —
 *  never the authorization code, which is single-use and secret. */
const SETUP_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function readStep(raw: string | null): WizardStep {
  return STEPS.find((step) => step === raw) ?? "start";
}

const HOW_IT_WORKS = "https://developers.facebook.com/documentation/business-messaging/whatsapp/overview";

function ConfigSkeleton() {
  return (
    <Card className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-6 sm:p-8" aria-hidden>
      <Skeleton className="mx-auto h-9 w-3/4" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-40 w-full rounded-card" />
      <Skeleton className="h-11 w-full rounded-tile" />
    </Card>
  );
}

/**
 * The connect wizard: one route, five URL-driven steps (`?step=start|meta|select|setup|done`).
 *
 * The URL holds the step and, from the moment setup starts, the server's `setup` id — so a reload,
 * the browser's Back button and "you can leave this page, setup keeps running" all do what they
 * say. It never holds the authorization code (single-use, and a credential while it lives), which
 * is passed from Meta's popup to `signup/complete` in memory only.
 *
 * A step that needs a setup id and has none (a pasted link, a bookmark from before) goes back to
 * the start rather than rendering a screen that cannot load.
 */
function WhatsAppConnect() {
  const [params, setParams] = useSearchParams();
  const step = readStep(params.get("step"));
  const rawSetup = params.get("setup");
  const setupId = rawSetup !== null && SETUP_ID.test(rawSetup) ? rawSetup : null;

  const configLoader = useCallback(() => getSignupConfig(), []);
  const config = useAsync(configLoader, []);

  const go = useCallback(
    (next: WizardStep, options: { setup?: string | null; replace?: boolean } = {}) => {
      setParams(
        (current) => {
          const nextParams = new URLSearchParams(current);
          nextParams.set("step", next);
          const setup = options.setup === undefined ? current.get("setup") : options.setup;
          if (setup === null) nextParams.delete("setup");
          else nextParams.set("setup", setup);
          return nextParams;
        },
        { replace: options.replace ?? false },
      );
    },
    [setParams],
  );

  const onSubmitted = useCallback(
    (out: SignupCompleteOut) => go("select", { setup: out.setup_id }),
    [go],
  );
  const signup = useMetaSignup(config.status === "success" ? config.data : null, onSubmitted);

  // Focus follows the step, so a screen-reader user hears the new question instead of staying on
  // a button that just left the page. Not on first paint: that is the page loading, not a step.
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousStep = useRef<WizardStep>(step);
  useEffect(() => {
    if (previousStep.current !== step) headingRef.current?.focus();
    previousStep.current = step;
  }, [step]);

  function continueWithMeta() {
    // Opened inside this click, so the browser counts the popup as requested.
    signup.start();
    go("meta");
  }

  const needsSetup = (step === "select" || step === "setup") && setupId === null;
  if (needsSetup) return <Navigate to="?step=start" replace />;

  return (
    <div className="rise flex flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-section text-ink">Connect WhatsApp</span>
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={HOW_IT_WORKS}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-11 items-center gap-1 rounded-tile px-3 text-sm font-semibold text-accent-ink hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            How this works
            <ArrowUpRight aria-hidden className="size-4" />
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
          <LinkButton to="/whatsapp">Exit setup</LinkButton>
        </div>
      </header>

      <ConnectProgress step={step} />

      {step === "start" || step === "meta" ? (
        <>
          {config.status === "loading" ? <ConfigSkeleton /> : null}
          {config.status === "error" ? (
            <LoadError error={config.error} title="Couldn't check whether Meta sign-in is available" onRetry={config.reload} />
          ) : null}
          {config.status === "success" && step === "start" ? (
            <ConnectStep
              config={config.data}
              signup={signup}
              onContinue={continueWithMeta}
              headingRef={headingRef}
            />
          ) : null}
          {config.status === "success" && step === "meta" ? (
            config.data.enabled ? (
              <MetaStep
                signup={signup}
                onReopen={signup.start}
                onBack={() => go("start")}
                headingRef={headingRef}
              />
            ) : (
              <Navigate to="?step=start" replace />
            )
          ) : null}
        </>
      ) : null}

      {step === "select" && setupId !== null ? (
        <SelectStep
          onBack={() => go("meta")}
          onChosen={() => go("setup")}
          onCheckSetup={() => go("setup")}
          headingRef={headingRef}
        />
      ) : null}

      {step === "setup" && setupId !== null ? (
        <SetupStep
          setupId={setupId}
          onBack={() => go("select")}
          onRestart={() => go("start", { setup: null })}
          onDone={() => go("done")}
          headingRef={headingRef}
        />
      ) : null}

      {step === "done" ? <DoneStep headingRef={headingRef} /> : null}

      {step === "start" && config.status === "success" && config.data.enabled ? (
        <p className="text-center text-body-sm text-muted">
          Prefer to paste an access token yourself?{" "}
          <Link to="/whatsapp/connection" className="font-semibold text-accent-ink underline underline-offset-2">
            Connect manually
          </Link>
          .
        </p>
      ) : null}
    </div>
  );
}

export function WhatsAppConnectPage() {
  return (
    <Announcer>
      <RequireGrant permission="engage.setup.whatsapp.write" what="WhatsApp setup">
        <WhatsAppConnect />
      </RequireGrant>
    </Announcer>
  );
}
