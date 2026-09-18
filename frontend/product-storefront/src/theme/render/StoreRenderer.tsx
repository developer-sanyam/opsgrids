import { NavLink } from "react-router-dom";

import type { Category, Product } from "@/api/types";
import { LineListProvider } from "@/store/LineListProvider";
import { STACK_WIDTH } from "../schema";
import type { PageDoc, StoreDoc } from "../schema";
import { pageForRoute, pagesInNav, resolveMode } from "../store";
import { DEFAULT_STORE_BASE, storeHref } from "./href";
import { contentColumnStyle } from "./pageGrid";
import { PageRenderer } from "./PageRenderer";
import { SectionEmpty, StoreText } from "./primitives";
import { ThemeFrame } from "./ThemeFrame";
import { T, step } from "./tokens";

export interface StoreRendererProps {
  store: StoreDoc;
  /** The `PageDoc.route` to render — `"/"`, `"/catalogue"`, … */
  route: string;
  products: Product[];
  categories: Category[];
  /** Canvas width in px; drives grid stacking. */
  width: number;
  /**
   * The URL prefix every in-store link is built against.
   *
   * `/store` is the console's framed preview and stays the default, so nothing already mounted
   * moves. `/live` is the buyer's own site: passing it is what makes the navigation point at live
   * addresses and — the part that was actually broken — what lets `NavLink` recognise the current
   * page there at all.
   */
  basePath?: string;
  /** Miniatures render the same tree at a smaller row unit and without chrome. */
  scale?: number;
  showChrome?: boolean;
}

/** The section that *is* a page's footer. A page carrying one gets no chrome footer under it. */
const FOOTER_SECTION = "footer-columns";

/**
 * One list of store pages, as links.
 *
 * The header and the footer draw the same thing at different weights, and which pages land in
 * which is `PageDoc.nav` — not a length cutoff, not the first N. A merchant with thirty-five
 * pages puts the five a buyer needs in the bar, the legal ones underneath, and the rest nowhere
 * at all; a merchant with the three a theme ships changes nothing and gets what they had.
 *
 * Renders nothing when the list is empty, so a store with no footer pages grows no empty landmark.
 */
function ChromeNav({
  pages,
  basePath,
  label,
  tone,
}: {
  pages: PageDoc[];
  basePath: string;
  label: string;
  tone: "primary" | "footer";
}) {
  if (pages.length === 0) return null;
  const primary = tone === "primary";
  return (
    <nav aria-label={label}>
      <ul
        style={{
          listStyle: "none",
          display: "flex",
          flexWrap: "wrap",
          gap: step(primary ? 0.9 : 0.75),
          margin: 0,
          padding: 0,
        }}
      >
        {pages.map((page) => (
          <li key={page.id}>
            <NavLink
              to={storeHref(page.route, basePath)}
              end
              style={({ isActive }) => ({
                fontFamily: T.fontBody,
                fontSize: primary ? "0.875rem" : "0.8125rem",
                fontWeight: primary ? 600 : 500,
                textDecoration: "none",
                color: isActive ? T.accent : T.muted,
                borderBottom: primary
                  ? `2px solid ${isActive ? T.accent : "transparent"}`
                  : undefined,
                paddingBottom: primary ? step(0.15) : undefined,
              })}
            >
              {page.name}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/**
 * The buyer-facing store: brand chrome, the routed page on the 12-column canvas, and a closing
 * line. This is what a dealer sees.
 *
 * **The outer wrapper is full width on purpose.** The content column now lives on the page grid's
 * middle tracks (`pageGrid.ts`), so a section can bleed past it; a `max-width` ancestor would be a
 * box no section could escape. The chrome — header and footer — takes that column back for itself
 * through `contentColumnStyle`, which draws the same gutter the grid's rails do, so the header's
 * left edge and a section's left edge agree to the pixel.
 */
export function StoreRenderer({
  store,
  route,
  products,
  categories,
  width,
  basePath = DEFAULT_STORE_BASE,
  scale = 1,
  showChrome = true,
}: StoreRendererProps) {
  const page = pageForRoute(store, route);
  const stacked = width < STACK_WIDTH;
  // Narrowed here, once, rather than read raw off a document that may predate the field.
  const mode = resolveMode(store);

  /*
   * One `contentinfo` per page.
   *
   * A merchant who places the tall `footer-columns` section has written their own footer, and the
   * chrome's one-line sign-off underneath it made two footer landmarks — which is not a styling nit
   * but the exact ambiguity landmarks exist to remove. Rendering the section as a `<div>` instead
   * would have inverted it: the merchant's footer *is* the footer, and the chrome's line is the
   * fallback for a page that has none.
   */
  const hasPageFooter = page?.sections.some((section) => section.type === FOOTER_SECTION) ?? false;

  /*
   * The chrome's two navs, and the one relationship between them and the `footer-columns` section.
   *
   * `primary` is the bar at the top; `footer` is a quiet row above the sign-off; `none` is a page
   * reachable only from a link someone wrote. Absent — every theme this build ships, every
   * document written before the field existed — reads as `primary`, so nothing already published
   * moves an inch.
   *
   * `nav` and the `footer-columns` section are two mechanisms, and they do not overlap. `nav`
   * places *a page* automatically. `footer-columns` is a footer a merchant *wrote*: hand-authored
   * link columns that can point at pages set to `none`, and at addresses this document cannot name
   * at all — a PDF, a WhatsApp number, an external portal. Neither subsumes the other and nothing
   * needs a third.
   *
   * Where both land on one page, the section owns the `contentinfo` — one page, one footer
   * landmark — and the chrome's footer nav drops below it as a plain legal strip. See the two
   * blocks at the end of this component.
   */
  const primaryPages = pagesInNav(store, "primary");
  const footerPages = pagesInNav(store, "footer");

  return (
    /*
     * The only place the line list is provided. Everything else that renders this tree — the
     * gallery's miniature, the editor's canvas, a card in a test — reaches `useLineList()`'s no-op
     * instead and behaves exactly as it did before the list existed.
     */
    <LineListProvider>
      <ThemeFrame tokens={store.tokens} style={{ minHeight: "100%" }}>
        <div
          style={{
            paddingBlock: step(stacked ? 1 : 1.5),
            display: "flex",
            flexDirection: "column",
            gap: step(1.5),
          }}
        >
          {showChrome ? (
            <div style={contentColumnStyle(stacked)}>
              <header
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: step(0.75),
                  paddingBottom: step(0.75),
                  borderBottom: `1px solid ${T.border}`,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: step(0.6), minWidth: 0 }}>
                  <span
                    aria-hidden
                    style={{
                      display: "grid",
                      placeItems: "center",
                      width: step(2),
                      height: step(2),
                      borderRadius: T.radius,
                      background: T.accent,
                      color: T.accentText,
                      fontFamily: T.fontHeading,
                      fontWeight: 800,
                      fontSize: "0.875rem",
                    }}
                  >
                    {store.brand.logoText.slice(0, 3).toUpperCase()}
                  </span>
                  <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.2, minWidth: 0 }}>
                    <span style={{ fontFamily: T.fontHeading, fontWeight: 800, fontSize: "1.0625rem", color: T.text }}>
                      {store.brand.name}
                    </span>
                    <span style={{ fontFamily: T.fontBody, fontSize: "0.75rem", color: T.muted }}>
                      {store.brand.tagline}
                    </span>
                  </span>
                </div>
                <ChromeNav pages={primaryPages} basePath={basePath} label="Store" tone="primary" />
              </header>
            </div>
          ) : null}

          <main>
            {page ? (
              <PageRenderer
                page={page}
                products={products}
                categories={categories}
                width={width}
                mode={mode}
                scale={scale}
              />
            ) : (
              <div style={contentColumnStyle(stacked)}>
                <SectionEmpty
                  title="This store has no page at that address"
                  hint="Add a page for this route in the editor, or pick another page from the navigation above."
                />
              </div>
            )}
          </main>

          {showChrome && !hasPageFooter ? (
            <div style={contentColumnStyle(stacked)}>
              <footer
                style={{
                  borderTop: `1px solid ${T.border}`,
                  paddingTop: step(0.75),
                  display: "flex",
                  flexDirection: "column",
                  gap: step(0.5),
                }}
              >
                <ChromeNav pages={footerPages} basePath={basePath} label="More" tone="footer" />
                <StoreText muted small>
                  {store.brand.name} · {store.brand.tagline}
                </StoreText>
              </footer>
            </div>
          ) : null}

          {/* The legal strip, under a footer the merchant wrote.
              A page carrying `footer-columns` owns the `contentinfo`, so this is a plain `<div>`
              and there is still exactly one footer landmark — but the pages placed in the footer
              still appear, which is the whole point of having placed them. Without this a merchant
              on a theme that ships `footer-columns` on every page (quickcart, atelier) would set
              "In the footer" and watch nothing happen anywhere in their store. */}
          {showChrome && hasPageFooter && footerPages.length > 0 ? (
            <div style={contentColumnStyle(stacked)}>
              <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: step(0.75) }}>
                <ChromeNav pages={footerPages} basePath={basePath} label="More" tone="footer" />
              </div>
            </div>
          ) : null}
        </div>
      </ThemeFrame>
    </LineListProvider>
  );
}
