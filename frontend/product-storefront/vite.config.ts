import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type Plugin } from "vite";
import { assetsInlineLimit } from "@opsgrids/config/vite";

/**
 * Two entry points, one app (ADR-0001, ADR-0012 §2).
 *
 * * `index.html` → `dist/` — the **merchant's console**: gallery, editor, delivery admin, the
 *   framed preview and `/live`. Built by `pnpm build`, unchanged by any of this.
 * * `buyer.html` → `dist-buyer/` — the **buyer's shop**, served by Caddy on a merchant's own
 *   domain. Built by `pnpm build:buyer` (`vite build --mode buyer`). It contains no auth code at
 *   all, because ADR-0001 scopes the refresh cookie to `.opsgrids.com` and a session therefore
 *   cannot exist on `merchant.com`.
 *
 * Two modes of one config rather than two config files: `tsconfig.json` typechecks
 * `vite.config.ts` by name, and a second file would sit outside that list — a build config nobody
 * typechecks is exactly where a silent mistake lives.
 *
 * Dev serves both: `/` is the console and `/buyer.html` is the shop, on the same proxy.
 */

/** Where the buyer build lands. `.gitignore`d; `infrastructure/edge/publish-web.sh` rsyncs it. */
const BUYER_OUT_DIR = "dist-buyer";

/**
 * Rename the buyer entry's HTML to `index.html` in the output.
 *
 * Vite names an emitted document after its path relative to the project root, so a `buyer.html`
 * input produces `dist-buyer/buyer.html`. The edge needs `index.html`: Caddy's SPA fallback is
 * `try_files {path} /index.html`, and `publish-web.sh` refuses to publish a directory without
 * one. Renaming the emitted asset is done here rather than by moving `buyer.html` into a
 * subdirectory, because the input's *name* is what makes `grep buyer.html` find this entry from
 * either end — which is precisely what the publish script greps for.
 */
function buyerIndexHtml(): Plugin {
  return {
    name: "storefront:buyer-index-html",
    enforce: "post",
    generateBundle(_options, bundle) {
      const emitted = bundle["buyer.html"];
      if (emitted === undefined) {
        throw new Error(
          "The buyer build emitted no buyer.html. The edge serves dist-buyer/index.html and " +
            "publish-web.sh refuses a directory without one, so this must not be allowed to pass.",
        );
      }
      delete bundle["buyer.html"];
      emitted.fileName = "index.html";
      bundle["index.html"] = emitted;
    },
  };
}

// One Vite app per product (ADR-0001). Dev on 4400 — clear of the marketing site on 4300 and of
// every product app below it.
export default defineConfig(({ mode }) => {
  const buyer = mode === "buyer";

  return {
    plugins: [react(), tailwindcss(), ...(buyer ? [buyerIndexHtml()] : [])],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    build: {
      assetsInlineLimit,
      ...(buyer
        ? {
            outDir: BUYER_OUT_DIR,
            emptyOutDir: true,
            rollupOptions: {
              input: fileURLToPath(new URL("./buyer.html", import.meta.url)),
            },
          }
        : {}),
    },
    server: {
      port: 4400,
      strictPort: true,
      // Same-origin API: the browser calls `/api/*`, which Vite forwards to the gateway on 8100.
      // This sidesteps CORS entirely — no gateway restart, no cross-origin preflight. It is the
      // same arrangement Caddy runs in production, which is why a custom domain adds no CORS
      // surface at all (ADR-0012 §2).
      proxy: {
        "/api": {
          target: "http://localhost:8100",
          changeOrigin: true,
        },
      },
    },
  };
});
