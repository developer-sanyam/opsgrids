/**
 * The buyer bundle's root: one bootstrap call, four states, and no router.
 *
 * ──────────────────────────────────────────────────────────────────────────────────────────
 * **The rule this component exists to enforce**, from ADR-0012 §2 and ADR-0010 §13:
 *
 * > A 404 means we looked and there is no shop at this address — render that, and offer **no**
 * > retry, because retrying cannot change it. **Anything else means we could not look** — render
 * > "we can't load this shop right now", and offer a retry.
 *
 * A single `catch` that renders "not found" for any failure reintroduces exactly the defect this
 * design exists to prevent, on the page a real buyer sees. The two branches below are therefore
 * driven by `StoreContextError.reason`, which only an HTTP 404 can set to `"not_found"`, and an
 * error that is *not* a `StoreContextError` — a bug in this bundle — falls to `"unavailable"`.
 * ──────────────────────────────────────────────────────────────────────────────────────────
 *
 * **There is no `AuthProvider` and no `RequireAuth` here, and there cannot be.** The refresh
 * cookie is scoped to `.opsgrids.com` (ADR-0001), so on a merchant's own domain a session cannot
 * exist: a guard would redirect to a sign-in page that cannot help, and the auth client would be
 * dead weight on the first-paint path with an XSS surface and nothing to gain. This is why
 * ADR-0012 §2 builds a second Vite entry instead of a route.
 *
 * **`main.buyer.tsx` does mount a bare `BrowserRouter`**, added once a theme could have more than
 * one page: `StoreRenderer`'s in-store navigation is `Link`/`NavLink`, which need a router to
 * move between a shop's pages at all. It carries no `@opsgrids/client` route table, no guard and
 * no auth-aware anything — just history.
 *
 * **The fetch is hand-rolled rather than `useAsync`.** The console's screens use the SDK's hook,
 * and importing it here would pull `@opsgrids/client`'s barrel — the auth provider, the guard,
 * the permission hooks and the session — into this module's graph and leave "did the bundler
 * shake it out" as the only thing standing between a buyer and the auth client. Twenty lines is
 * a cheaper guarantee than a tree-shaking assumption.
 */

import { useCallback, useEffect, useState } from "react";

import { Button, Card, Skeleton } from "@opsgrids/ui";

import {
  currentHost,
  loadStoreContext,
  StoreContextError,
  type StoreContextFailure,
  type StoreContextOut,
} from "@/api/storeContext";
import { canonicalHref, useCanonicalLink, useDocumentTitle } from "./canonical";
import {
  LOADING_STATUS,
  NOT_FOUND_BODY,
  NOT_FOUND_TITLE,
  RETRY_LABEL,
  UNAVAILABLE_BODY,
  UNAVAILABLE_OFFLINE_BODY,
  UNAVAILABLE_TITLE,
} from "./copy";
import { ShopFront } from "./ShopFront";

/* Imported from `@opsgrids/ui` directly rather than through this app's `@/ui` barrel: that barrel
   also re-exports the console's sidebar, device toggle and theme preview, and the last of those
   reaches the whole theme library. None of it belongs in a bundle a stranger downloads. */

type BootstrapState =
  | { status: "loading" }
  | { status: "ready"; context: StoreContextOut }
  | { status: "failed"; reason: StoreContextFailure };

/** A full-viewport surface for the two states that have no shop to draw. */
function BuyerNotice({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="grid min-h-dvh place-items-center bg-ground p-4 text-ink">
      <Card className="w-full max-w-md p-6">
        <h1 className="display text-xl font-extrabold">{title}</h1>
        <p className="mt-2 text-sm text-ink-soft">{body}</p>
        {action === undefined ? null : <div className="mt-6">{action}</div>}
      </Card>
    </div>
  );
}

/**
 * The skeleton, at the geometry the shop will take.
 *
 * A 40px mark and two text lines totalling 48px inside the same padding as `ShopHeader`, so the
 * header does not move when the real brand arrives. `aria-hidden`: the status line below is what
 * announces the wait, and a screen reader has no use for the shape of a placeholder.
 */
function ShopSkeleton() {
  return (
    <div className="flex min-h-dvh flex-col bg-ground" aria-hidden>
      <div className="border-b border-line bg-surface">
        <div className="mx-auto flex w-full max-w-5xl items-center gap-3 px-4 py-4">
          <Skeleton className="size-10 shrink-0 rounded-tile" />
          <div className="flex min-w-0 flex-col">
            <Skeleton className="h-7 w-40 rounded-tile" />
            <Skeleton className="h-5 w-56 rounded-tile" />
          </div>
        </div>
      </div>
      <div className="mx-auto w-full max-w-5xl flex-1 px-4 py-16">
        <Skeleton className="h-7 w-64 rounded-tile" />
        <Skeleton className="mt-2 h-5 w-full max-w-prose rounded-tile" />
      </div>
    </div>
  );
}

/**
 * Offline and "the server is unwell" are the same fact to a buyer — we could not look — but not
 * the same remedy, so the sentence differs while the affordance does not.
 */
function unavailableBody(): string {
  return typeof navigator !== "undefined" && navigator.onLine === false
    ? UNAVAILABLE_OFFLINE_BODY
    : UNAVAILABLE_BODY;
}

export function BuyerApp() {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<BootstrapState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    /* `window.location.hostname` — no port, no scheme, nothing the bundle was built with. The
       server normalises and refuses; this side sends the browser's own value and nothing else. */
    loadStoreContext(currentHost()).then(
      (context) => {
        if (!cancelled) setState({ status: "ready", context });
      },
      (error: unknown) => {
        if (cancelled) return;
        /* The one branch in this bundle that may produce "no shop here", and it reads a reason
           only a 404 can set. An unexpected throw is a failure to look, not an absence. */
        const reason = error instanceof StoreContextError ? error.reason : "unavailable";
        setState({ status: "failed", reason });
      },
    );

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);

  const context = state.status === "ready" ? state.context : null;
  useDocumentTitle(context === null ? null : context.name);
  useCanonicalLink(
    context === null ? null : canonicalHref(context.canonical_host, window.location.pathname),
  );

  /* One live region for the whole app, mounted in every state so an assistive technology is
     watching the same node before the text changes rather than being handed a new one. */
  const announcement =
    state.status === "loading"
      ? LOADING_STATUS
      : state.status === "ready"
        ? state.context.name
        : state.reason === "not_found"
          ? NOT_FOUND_TITLE
          : UNAVAILABLE_TITLE;

  return (
    <>
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      {state.status === "loading" ? <ShopSkeleton /> : null}
      {state.status === "ready" ? <ShopFront context={state.context} /> : null}
      {state.status === "failed" && state.reason === "not_found" ? (
        /* Deliberately no retry: the server looked. A button here would invite a buyer to keep
           asking a question that has been answered. */
        <BuyerNotice title={NOT_FOUND_TITLE} body={NOT_FOUND_BODY} />
      ) : null}
      {state.status === "failed" && state.reason === "unavailable" ? (
        <BuyerNotice
          title={UNAVAILABLE_TITLE}
          body={unavailableBody()}
          action={
            <Button variant="dark" onClick={retry}>
              {RETRY_LABEL}
            </Button>
          }
        />
      ) : null}
    </>
  );
}
