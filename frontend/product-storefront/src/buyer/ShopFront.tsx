/**
 * The shop, as a buyer meets it on the merchant's own address.
 *
 * ──────────────────────────────────────────────────────────────────────────────────────────
 * **What this can draw today, and why it is not the theme renderer.**
 *
 * `StoreRenderer` needs a `StoreDoc` — twelve `ThemeTokens` and a list of `PageDoc`s. **No
 * public route in `libs/contracts/openapi/storefront.json` returns either.** The store document
 * lives behind `GET /api/v1/storefront/store`, which is the merchant's authenticated route, and
 * ADR-0001 scopes the refresh cookie to `.opsgrids.com` — so a buyer on `merchant.com` can never
 * call it, by construction rather than by omission.
 *
 * `StoreContextOut` is the only public representation of the brand, and the storefront-document
 * ADR **extends that payload** rather than adding a second one (ADR-0012 §11). When it does, the
 * tokens and the pages arrive in the same bootstrap this component already has, and the `<main>`
 * below hands them to `StoreRenderer`. Until then the honest surface is the brand and an empty
 * state that says so.
 * ──────────────────────────────────────────────────────────────────────────────────────────
 *
 * **Escaping.** `name`, `tagline` and `logo_text` are merchant-authored and returned verbatim —
 * the service trims, bounds and strips control characters and nothing else. Every one of them is
 * rendered as a React text child, which escapes. There is no `dangerouslySetInnerHTML` on this
 * surface and none may be added: `BuyerApp.test.tsx` renders a shop called `<b>Verma</b> & Sons`
 * and asserts those characters appear as text with no element built from them.
 *
 * **This is pre-theme chrome, not a store page.** It styles itself from the app's own tokens
 * because the merchant's palette has not arrived yet, and it spends none of the Console accent —
 * a violet button on a merchant's shop front is the exact mistake `theme/render/`'s isolation
 * rule exists to prevent.
 */

import type { StoreContextOut } from "@/api/storeContext";
import { NO_PAGES_BODY, NO_PAGES_TITLE, otherAddressNotice } from "./copy";

/**
 * The mark in the corner. **`logo_text` falls back to `name`** — both are nullable-by-contract in
 * the sense that `logo_text` may be `null`, and the server deliberately does not copy one into
 * the other, leaving the fallback to whatever renders the page. Three characters is what the
 * store chrome already uses for the same mark.
 */
function markFor(context: StoreContextOut): string {
  return (context.logo_text ?? context.name).trim().slice(0, 3).toUpperCase();
}

/**
 * The brand bar.
 *
 * Its geometry is shared with the loading skeleton — a 40px mark and two text lines inside the
 * same padding — so the switch from skeleton to shop moves nothing on the page.
 */
export function ShopHeader({ context }: { context: StoreContextOut }) {
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex w-full max-w-5xl items-center gap-3 px-4 py-4">
        <span
          aria-hidden
          className="grid size-10 shrink-0 place-items-center rounded-tile bg-ink text-sm font-extrabold text-surface"
        >
          {markFor(context)}
        </span>
        <div className="min-w-0">
          <h1 className="display truncate text-lg font-extrabold">{context.name}</h1>
          {context.tagline === null ? null : (
            <p className="truncate text-sm text-muted">{context.tagline}</p>
          )}
        </div>
      </div>
    </header>
  );
}

export function ShopFront({ context }: { context: StoreContextOut }) {
  return (
    <div className="flex min-h-dvh flex-col bg-ground text-ink">
      <ShopHeader context={context} />

      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center px-4 py-16">
        <div className="max-w-prose">
          <h2 className="display text-xl font-extrabold">{NO_PAGES_TITLE}</h2>
          <p className="mt-2 text-sm text-ink-soft">{NO_PAGES_BODY}</p>
        </div>
      </main>

      {/* The canonical pointer, as a line and never as a redirect (ADR-0012 §8). `is_canonical`
          is computed from the live domain state, so a failing custom domain makes the platform
          host canonical again — which is precisely when pushing a buyer at the other address
          would send them to a name that does not answer. */}
      {context.is_canonical ? null : (
        <footer className="border-t border-line bg-surface">
          <p className="mx-auto w-full max-w-5xl px-4 py-3 text-xs text-muted">
            {otherAddressNotice(context.canonical_host)}
          </p>
        </footer>
      )}
    </div>
  );
}
