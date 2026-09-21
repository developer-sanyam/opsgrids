/**
 * The buyer bundle's entry point — `buyer.html` → here → `dist-buyer/` (ADR-0012 §2).
 *
 * **This is the second Vite entry, and its defining property is what it does not contain.** No
 * `AuthProvider`, no `RequireAuth`, no login screen, no org switcher, no editor, no delivery
 * admin, no router. The refresh cookie is scoped to `.opsgrids.com` (ADR-0001), so on a
 * merchant's own domain a session cannot exist — shipping the auth client to every anonymous
 * shopper would be dead weight on the first-paint path that could never work, plus an XSS surface
 * with nothing to gain. `main.tsx` stays exactly as it is and remains the merchant's console.
 *
 * The one thing shared with the console is `index.css`: the Tailwind layer and the design
 * system's tokens. A token file is not chrome, and duplicating it is how two bundles start
 * disagreeing about what a border is.
 *
 * `configureSession` is deliberately absent. It names this app's session slot; a bundle with no
 * session has no slot to name.
 *
 * **Three things `buyer.html` omits that `index.html` carries**, recorded here rather than in a
 * comment that ships to every buyer:
 *
 * * **No description meta.** The description belongs to the merchant's shop, and this bundle is
 *   byte-identical for every merchant — a baked-in one would put OpsGrids' own marketing copy on
 *   fifty different shops. It arrives with the store document.
 * * **No theme-restoring script.** `localStorage["opsgrids.storefront.theme"]` is the merchant's
 *   *console* preference. A buyer has never seen the console, and reading a console key on a
 *   merchant's own domain is reading storage that is not ours to read.
 * * **No web-font link.** Two preconnects and a render-blocking stylesheet on the first paint of
 *   a page whose real typography arrives with the merchant's theme. The token layer names Manrope
 *   first and falls back to the system face, which is the right trade before the shop's own fonts
 *   are known.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import "./index.css";
import { BuyerApp } from "./buyer/BuyerApp";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    {/* Not an auth boundary or a multi-screen router — the bundle still has neither. This is
        the theme renderer's own requirement: `StoreRenderer`'s in-store navigation is built from
        `Link`/`NavLink`, so a store with more than one page needs a router to move between them
        on a buyer's own domain, at the domain's own root (`ShopFront`'s `basePath=""`). */}
    <BrowserRouter>
      <BuyerApp />
    </BrowserRouter>
  </StrictMode>,
);
