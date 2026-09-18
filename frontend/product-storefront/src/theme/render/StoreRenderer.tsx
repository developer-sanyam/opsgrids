import { NavLink } from "react-router-dom";

import type { Category, Product } from "@/api/types";
import { STACK_WIDTH } from "../schema";
import type { StoreDoc } from "../schema";
import { pageForRoute } from "../store";
import { storeHref } from "./href";
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
  /** Miniatures render the same tree at a smaller row unit and without chrome. */
  scale?: number;
  showChrome?: boolean;
}

/**
 * The buyer-facing store: brand chrome, the routed page on the 12-column canvas, and a closing
 * line. This is what a dealer sees.
 */
export function StoreRenderer({
  store,
  route,
  products,
  categories,
  width,
  scale = 1,
  showChrome = true,
}: StoreRendererProps) {
  const page = pageForRoute(store, route);
  const stacked = width < STACK_WIDTH;

  return (
    <ThemeFrame tokens={store.tokens} style={{ minHeight: "100%" }}>
      <div
        style={{
          maxWidth: T.maxWidth,
          margin: "0 auto",
          padding: `${step(stacked ? 1 : 1.5)} ${step(stacked ? 0.75 : 1.5)}`,
          display: "flex",
          flexDirection: "column",
          gap: step(1.5),
        }}
      >
        {showChrome ? (
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
            <nav aria-label="Store">
              <ul
                style={{
                  listStyle: "none",
                  display: "flex",
                  flexWrap: "wrap",
                  gap: step(0.9),
                  margin: 0,
                  padding: 0,
                }}
              >
                {store.pages.map((candidate) => (
                  <li key={candidate.id}>
                    <NavLink
                      to={storeHref(candidate.route)}
                      end
                      style={({ isActive }) => ({
                        fontFamily: T.fontBody,
                        fontSize: "0.875rem",
                        fontWeight: 600,
                        textDecoration: "none",
                        color: isActive ? T.accent : T.muted,
                        borderBottom: `2px solid ${isActive ? T.accent : "transparent"}`,
                        paddingBottom: step(0.15),
                      })}
                    >
                      {candidate.name}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </nav>
          </header>
        ) : null}

        <main>
          {page ? (
            <PageRenderer
              page={page}
              products={products}
              categories={categories}
              width={width}
              scale={scale}
            />
          ) : (
            <SectionEmpty
              title="This store has no page at that address"
              hint="Add a page for this route in the editor, or pick another page from the navigation above."
            />
          )}
        </main>

        {showChrome ? (
          <footer style={{ borderTop: `1px solid ${T.border}`, paddingTop: step(0.75) }}>
            <StoreText muted small>
              {store.brand.name} · {store.brand.tagline}
            </StoreText>
          </footer>
        ) : null}
      </div>
    </ThemeFrame>
  );
}
