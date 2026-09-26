import { ArrowUpRight, Card, EmptyState, ErrorState, MailIcon } from "@opsgrids/ui";

import { dashboardProductsUrl } from "@/lib/dashboard";
import { isNotActivated } from "@/lib/errors";

/**
 * Engage is not active for this organization — what every screen shows when the gateway answers
 * its read with a `402`.
 *
 * Not an error, and deliberately not dressed as one: no alert role, no danger heading, no
 * "couldn't load". Nobody did anything wrong; the product simply has not been started, and this
 * says where it is started. That is the dashboard's Products page, on its own host — activation
 * follows the subscription bought there, so no control in this app could turn Engage on, and a
 * button here pretending to would be a second, weaker door.
 *
 * The sign-in note is not a hedge. The gateway decides from the entitlements inside the access
 * token, and a token minted before Engage was started does not carry it until it is refreshed.
 */
export function EngageNotActive() {
  return (
    <Card className="p-6">
      <EmptyState
        icon={<MailIcon className="size-6" />}
        title="Engage isn't active for this organization yet"
        description="Someone who manages your organization's products, usually an owner or admin, can start it from Products in the OpsGrids dashboard. Once it's on, come back here to set up sending — you may be asked to sign in again."
        action={
          <a
            href={dashboardProductsUrl()}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-full bg-accent px-5 text-[0.95rem] font-semibold text-white transition-colors duration-150 hover:bg-accent-strong active:bg-accent-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Open Products in the dashboard
            <ArrowUpRight className="size-4" aria-hidden />
          </a>
        }
      />
    </Card>
  );
}

/**
 * What a screen shows when its read fails: the not-active state for a `402`, and the error it
 * always showed for anything else. Every Engage read goes through the gateway's `engage` prefix,
 * so every screen can meet the `402` — and routing them all through here is what keeps it one
 * state rather than four copies drifting apart.
 *
 * `title` is the screen's own heading for a real failure ("Couldn't load your domains"). A `500`
 * or a network failure is a real failure and keeps it. WhatsApp templates used to be the one
 * screen that could see a `notification` `402` instead of an `engage` one — since it moved to
 * `GET /engage/whatsapp/templates`, every screen's `402` means the same thing, so there is
 * nothing left to tell `LoadFailure` apart from a `notActive` prop for.
 */
export function LoadFailure({ error, title }: { error: Error; title: string }) {
  if (isNotActivated(error)) return <EngageNotActive />;
  return (
    <Card className="p-6">
      <ErrorState message={error.message} title={title} />
    </Card>
  );
}
