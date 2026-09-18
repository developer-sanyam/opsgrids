/**
 * The four retail sections.
 *
 * Each one has to come out of `settings` alone — no catalogue, no context, no knowledge of the
 * page it is on — so every test here hands it an empty `products` and `categories` list. What is
 * pinned beyond "it renders" is the small set of decisions that are expensive to get wrong later:
 * the landmarks, the search form actually being a form, and empty numbered slots leaving no hole.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { defaultSettings } from "../sections";
import type { SettingValue } from "../schema";
import type { StoreMode } from "../schema";
import { AnnouncementBar } from "./AnnouncementBar";
import { CollectionRow } from "./CollectionRow";
import { FooterColumns } from "./FooterColumns";
import { PromoBanner } from "./PromoBanner";
import type { SectionComponent } from "./types";

const PHONE_WIDTH = 390;
const DESKTOP_WIDTH = 1200;

/** Where the router ended up, so a navigation can be asserted without a real browser. */
function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location">{`${location.pathname}${location.search}`}</p>;
}

function renderSection(
  Component: SectionComponent,
  type: string,
  overrides: Record<string, SettingValue> = {},
  { width = DESKTOP_WIDTH, mode = "retail" as StoreMode } = {},
) {
  return render(
    <MemoryRouter initialEntries={["/store"]}>
      <Component
        settings={{ ...defaultSettings(type), ...overrides }}
        products={[]}
        categories={[]}
        stacked={width < 768}
        width={width}
        mode={mode}
      />
      <LocationProbe />
    </MemoryRouter>,
  );
}

/* ----------------------------- announcement-bar ---------------------------- */

describe("the announcement bar", () => {
  it("is a labelled region, not a second banner landmark", () => {
    renderSection(AnnouncementBar, "announcement-bar");

    // The store chrome already renders the page's one `<header>`. Two banners makes "jump to the
    // banner" ambiguous, which is the single thing landmarks exist to prevent.
    expect(screen.queryByRole("banner")).toBeNull();
    expect(screen.getByRole("region", { name: "Delivery and search" })).not.toBeNull();
  });

  it("shows the merchant's promise verbatim and never derives one", () => {
    renderSection(AnnouncementBar, "announcement-bar", { promiseText: "Delivery in 8 minutes" });

    expect(screen.getByText("Delivery in 8 minutes")).not.toBeNull();
  });

  it("gives the search box a real form, an accessible name and a submit control", () => {
    renderSection(AnnouncementBar, "announcement-bar");

    const form = screen.getByRole("search", { name: "Search the catalogue" });
    expect(within(form).getByRole("searchbox", { name: "Search the catalogue" })).not.toBeNull();
    expect(within(form).getByRole("button", { name: "Search the catalogue" })).not.toBeNull();
  });

  it("navigates to the results page with what was typed", async () => {
    const user = userEvent.setup();
    renderSection(AnnouncementBar, "announcement-bar");

    await user.type(screen.getByRole("searchbox"), "toor dal{Enter}");

    expect(screen.getByTestId("location").textContent).toBe("/store/catalogue?q=toor%20dal");
  });

  it("still navigates on an empty search rather than sitting there doing nothing", async () => {
    const user = userEvent.setup();
    renderSection(AnnouncementBar, "announcement-bar");

    await user.click(screen.getByRole("button", { name: "Search the catalogue" }));

    expect(screen.getByTestId("location").textContent).toBe("/store/catalogue");
  });

  it("keeps the search form at 390, where it drops to its own row", () => {
    renderSection(AnnouncementBar, "announcement-bar", {}, { width: PHONE_WIDTH });

    expect(screen.getByRole("search", { name: "Search the catalogue" })).not.toBeNull();
    expect(screen.getAllByRole("searchbox")).toHaveLength(1);
  });

  it("drops the search entirely when the merchant turns it off", () => {
    renderSection(AnnouncementBar, "announcement-bar", { showSearch: false });

    expect(screen.queryByRole("search")).toBeNull();
  });

  it("states the basket as text, not as a link to a page that does not exist", () => {
    renderSection(AnnouncementBar, "announcement-bar");

    expect(screen.getByText("My cart")).not.toBeNull();
    expect(screen.getByText("0 items")).not.toBeNull();
    expect(screen.queryByRole("link", { name: /cart/i })).toBeNull();
  });

  it("uses the trade vocabulary on a trade store", () => {
    renderSection(AnnouncementBar, "announcement-bar", {}, { mode: "trade" });

    expect(screen.getByText("Line list")).not.toBeNull();
    expect(screen.getByText("0 lines")).not.toBeNull();
  });

  it("renders from an empty settings bag", () => {
    const { container } = renderSection(AnnouncementBar, "announcement-bar", {});
    expect(container.querySelector("section")).not.toBeNull();
  });
});

/* ------------------------------ collection-row ----------------------------- */

describe("the collection row", () => {
  it("skips an empty numbered slot instead of drawing a hole", () => {
    renderSection(CollectionRow, "collection-row");

    // The fourth slot ships empty by default; three tiles, not four with a blank.
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Grocery & Kitchen" })).not.toBeNull();
  });

  it("shows a fourth tile once the merchant fills it in", () => {
    renderSection(CollectionRow, "collection-row", {
      item4Title: "Beauty",
      item4Href: "/store/catalogue",
    });

    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByRole("link", { name: "Beauty" })).not.toBeNull();
  });

  it("renders a tile with no destination as a picture, never as a dead link", () => {
    renderSection(CollectionRow, "collection-row", { item1Href: "" });

    expect(screen.queryByRole("link", { name: "Grocery & Kitchen" })).toBeNull();
    expect(screen.getByText("Grocery & Kitchen")).not.toBeNull();
  });

  it("offers its own empty state when every slot is blank", () => {
    renderSection(CollectionRow, "collection-row", {
      item1Title: "",
      item2Title: "",
      item3Title: "",
      item4Title: "",
    });

    expect(screen.getByText("No collections yet")).not.toBeNull();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("names the track when it scrolls, so it is reachable and announced", () => {
    renderSection(CollectionRow, "collection-row", { scroll: true }, { width: PHONE_WIDTH });

    const track = screen.getByRole("list", { name: "Shop by store — scrollable row" });
    expect(track.getAttribute("tabindex")).toBe("0");
  });

  it("is a plain grid when the merchant has not asked for a scroller", () => {
    renderSection(CollectionRow, "collection-row", {}, { width: PHONE_WIDTH });

    expect(screen.queryByRole("list", { name: /scrollable/ })).toBeNull();
  });
});

/* ------------------------------- promo-banner ------------------------------ */

describe("the promo banner", () => {
  it("never emits an h1 — the hero owns the page's only one", () => {
    const { container } = renderSection(PromoBanner, "promo-banner");

    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector("h2")?.textContent).toBe("Monsoon essentials, delivered");
  });

  it("skips an empty tile slot", () => {
    renderSection(PromoBanner, "promo-banner");

    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("draws no tile list at all when every slot is blank", () => {
    renderSection(PromoBanner, "promo-banner", {
      tile1Title: "",
      tile2Title: "",
      tile3Title: "",
      tile4Title: "",
    });

    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.getByRole("heading", { level: 2 })).not.toBeNull();
  });

  it("renders at 390 with the heading and the call to action intact", () => {
    renderSection(PromoBanner, "promo-banner", {}, { width: PHONE_WIDTH });

    expect(screen.getByRole("heading", { level: 2 })).not.toBeNull();
    expect(screen.getByRole("link", { name: "Shop the edit" })).not.toBeNull();
  });
});

/* ------------------------------ footer-columns ----------------------------- */

describe("the footer columns", () => {
  it("renders exactly one contentinfo landmark", () => {
    renderSection(FooterColumns, "footer-columns");

    expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
  });

  it("turns Label | /href into a link and a bare line into text", () => {
    renderSection(FooterColumns, "footer-columns");

    expect(screen.getByRole("link", { name: "Seating" }).getAttribute("href")).toBe("/store/catalogue");
    // The contact column's opening hours have no pipe. They are information, not a destination.
    expect(screen.getByText("Mon to Sat, 10am to 7pm")).not.toBeNull();
    expect(screen.queryByRole("link", { name: "Mon to Sat, 10am to 7pm" })).toBeNull();
  });

  it("leaves a tel: and a mailto: as plain anchors the router cannot swallow", () => {
    renderSection(FooterColumns, "footer-columns");

    expect(screen.getByRole("link", { name: "+91 291 244 0000" }).getAttribute("href")).toBe(
      "tel:+912912440000",
    );
    expect(screen.getByRole("link", { name: "hello@example.in" }).getAttribute("href")).toBe(
      "mailto:hello@example.in",
    );
  });

  it("writes payment methods as words, with no image anywhere", () => {
    const { container } = renderSection(FooterColumns, "footer-columns");

    expect(screen.getByText("We accept")).not.toBeNull();
    for (const method of ["UPI", "Visa", "Mastercard", "RuPay", "Net banking"]) {
      expect(screen.getByText(method), method).not.toBeNull();
    }
    expect(container.querySelector("img")).toBeNull();
  });

  it("skips a column the merchant emptied", () => {
    renderSection(FooterColumns, "footer-columns", { col3Title: "", col3Links: "" });

    expect(screen.queryByText("Company")).toBeNull();
    expect(screen.getByText("Shop")).not.toBeNull();
  });

  it("keeps a sane heading order: one h2 for the about block, h3 for each column", () => {
    renderSection(FooterColumns, "footer-columns");

    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 3 }).length).toBeGreaterThan(1);
  });

  it("says what is missing when the merchant clears the whole thing", () => {
    renderSection(FooterColumns, "footer-columns", {
      aboutTitle: "",
      aboutText: "",
      col1Title: "",
      col1Links: "",
      col2Title: "",
      col2Links: "",
      col3Title: "",
      col3Links: "",
      col4Title: "",
      col4Links: "",
      socialTitle: "",
      socialLinks: "",
      paymentTitle: "",
      paymentMethods: "",
      legalText: "",
    });

    expect(screen.getByText("Nothing in the footer yet")).not.toBeNull();
  });

  it("renders at 390 without losing a column's content", () => {
    renderSection(FooterColumns, "footer-columns", {}, { width: PHONE_WIDTH });

    expect(screen.getByText("Shop")).not.toBeNull();
    expect(screen.getByText("Contact")).not.toBeNull();
    expect(screen.getByRole("link", { name: "Returns" })).not.toBeNull();
  });
});
