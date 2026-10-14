import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { ShieldIcon } from "../icons";
import { Card } from "./Card";
import { PageHeader } from "./PageHeader";
import { EmptyState, Skeleton } from "./States";

/**
 * What shows while a lazy-loaded route's chunk downloads. Reserves roughly the shape a loaded
 * screen occupies — a header-height bar over a tall card — so the swap from fallback to the real
 * screen does not shift the page under a reader who has already started scanning it.
 */
export function RouteFallback() {
  return (
    <div className="rise flex flex-col gap-6">
      <Skeleton className="h-9 w-48 rounded-tile" />
      <Card className="p-6">
        <Skeleton className="h-64 w-full rounded-tile" />
      </Card>
    </div>
  );
}

const BACK_LINK =
  "inline-flex h-11 items-center rounded-full bg-accent px-6 font-semibold text-on-accent transition-colors hover:bg-accent-strong";

interface NotFoundScreenProps {
  /** The sentence under the heading. Defaults to the wording most products use. */
  description?: string | undefined;
  /** The way back, which is the app's landing screen. */
  backTo?: string | undefined;
  backLabel?: string | undefined;
}

/**
 * The route table's catch-all: an address nothing serves. It sits inside the signed-in shell, so
 * the way back is the app's landing screen. Nothing loads here, so it has no loading, empty, error
 * or forbidden state — a dead link is the whole of its job.
 */
export function NotFoundScreen({
  description = "Check the address, or head back to the overview.",
  backTo = "/",
  backLabel = "Back to overview",
}: NotFoundScreenProps) {
  return (
    <div className="rise flex justify-center pt-8">
      <Card className="flex w-full max-w-lg flex-col items-center gap-4 p-8 text-center">
        <div className="flex flex-col gap-2">
          <span className="text-sm font-semibold uppercase tracking-wide text-muted">Not found</span>
          <h1 className="display text-3xl font-extrabold">That page doesn't exist</h1>
          <p className="mx-auto max-w-sm text-sm leading-relaxed text-muted">{description}</p>
        </div>
        <Link to={backTo} className={BACK_LINK}>
          {backLabel}
        </Link>
      </Card>
    </div>
  );
}

interface ComingSoonPageProps {
  icon?: ReactNode | undefined;
  title: string;
  description: string;
  /** Replaces the "Coming soon" eyebrow — a 404 is not a promise of a future feature. */
  eyebrow?: string | undefined;
}

/** A placeholder for a surface that is not built yet, or a catch-all that has nothing to say. */
export function ComingSoonPage({ icon, title, description, eyebrow = "Coming soon" }: ComingSoonPageProps) {
  return (
    <div className="rise flex justify-center pt-8">
      <Card className="flex w-full max-w-lg flex-col items-center gap-6 p-8 text-center">
        {icon ? (
          <span className="grid size-16 place-items-center rounded-full bg-accent-soft text-accent-ink">
            {icon}
          </span>
        ) : null}
        <div className="flex flex-col gap-2">
          <span className="text-sm font-semibold uppercase tracking-wide text-muted">{eyebrow}</span>
          <h1 className="display text-3xl font-extrabold">{title}</h1>
          <p className="mx-auto max-w-sm text-sm leading-relaxed text-muted">{description}</p>
        </div>
        <Link to="/" className={BACK_LINK}>
          Back to overview
        </Link>
      </Card>
    </div>
  );
}

interface PermissionDeniedProps {
  /** What the screen is, lowercase: "campaigns", "the audit log". */
  what: string;
  /** Who can fix it. Ends the sentence "Reload the page; if it persists, …". */
  remedy?: string | undefined;
}

/**
 * What a screen shows when the caller is known not to hold the permission it needs.
 *
 * It is a state, not an error: nothing failed, and a role is not something this screen can fix.
 * It is also **presentation, not protection** — the route is reachable by URL either way and every
 * endpoint behind it refuses the call on its own, which is why the copy names the role rather than
 * claiming the data is hidden. `RequirePermission` renders this on a failed grants fetch as well as
 * on a real denial, so the sentence covers both without asserting which one it is.
 *
 * It is a whole page, so it carries the page's `h1`: a page with none leaves a screen-reader
 * user's heading navigation with nothing to land on.
 */
export function PermissionDenied({
  what,
  remedy = "someone who manages roles in your organization can add the permission.",
}: PermissionDeniedProps) {
  const name = `${what.charAt(0).toUpperCase()}${what.slice(1)}`;
  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title={name} />
      <Card className="p-6">
        <EmptyState
          icon={<ShieldIcon className="size-6" />}
          title={`You can't open ${what} right now`}
          description={`Either your role doesn't include seeing ${what}, or your access couldn't be checked. Reload the page; if it persists, ${remedy}`}
        />
      </Card>
    </div>
  );
}
