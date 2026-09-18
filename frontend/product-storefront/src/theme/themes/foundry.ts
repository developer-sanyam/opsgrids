/**
 * Foundry — industrial and manufacturing supply.
 *
 * Square corners, a system-sans set tight, safety red spent only on the primary action, and a
 * black hero band. The home page leads with proof (who they supply) before it leads with product,
 * because an OEM buyer qualifies the supplier first.
 */

import type { Theme } from "../schema";
import { page, sec } from "./build";

export const foundry: Theme = {
  key: "foundry",
  name: "Foundry",
  description:
    "Square, dense and unsentimental. A black hero band, safety red on one action, and proof above product.",
  industry: "Industrial & manufacturing",
  mode: "trade",
  tokens: {
    colorBg: "#F2F3F5",
    colorSurface: "#FFFFFF",
    colorText: "#14171A",
    colorMuted: "#5A6169",
    colorAccent: "#B3261E",
    colorAccentText: "#FFFFFF",
    colorBorder: "#D6DADF",
    fontHeading: 'system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    fontBody: 'system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    radius: 2,
    space: 16,
    maxWidth: 1240,
  },
  pages: [
    page("fdy-home", "/", "Home", [
      sec("fdy-hero", "hero", { x: 0, y: 0, w: 12, h: 9 }, {
        eyebrow: "Established 1994",
        heading: "The fastener and bearing shelf your line runs on",
        body: "Twelve thousand stocked SKUs, dispatched the same working day from Bhiwandi, Coimbatore and Ludhiana.",
        ctaLabel: "Browse the catalogue",
        secondaryLabel: "Talk to a regional manager",
        tone: "contrast",
        align: "left",
      }),
      sec("fdy-logos", "logo-wall", { x: 0, y: 9, w: 12, h: 4 }, {
        heading: "On approved vendor lists at",
        names: "Godrej & Boyce\nThermax\nKirloskar Brothers\nAshok Leyland\nBharat Forge\nCummins India",
      }),
      sec("fdy-cats", "category-tiles", { x: 0, y: 13, w: 12, h: 7 }, {
        heading: "Shop by category",
        columns: 4,
        limit: 8,
        /* Two across on a phone, not the library's four. These word-only tiles carry a category
           name like "Bearings & Power Transmission"; four across at 390px leaves about eighty
           pixels for it, which is legible but cramped. The photographic styles a retail theme uses
           are what four across was widened for. */
        mobileColumns: 2,
      }),
      sec("fdy-grid", "product-grid", { x: 0, y: 20, w: 12, h: 12 }, {
        heading: "Fast-moving lines",
        source: "featured",
        limit: 8,
        columns: 4,
      }),
      sec("fdy-features", "feature-columns", { x: 0, y: 32, w: 12, h: 6 }, {
        heading: "How we work",
        columns: 3,
        item1Title: "3pm cut-off",
        item1Body: "Stocked lines ordered before 3pm IST leave the same day, from whichever warehouse is nearest you.",
        item2Title: "Certificates travel",
        item2Body: "Mill and batch certificates are in the consignment, not in an email a week after the audit.",
        item3Title: "Net 30 on approval",
        item3Body: "Trade accounts clear in two working days. Standing POs for repeat lines are welcome.",
      }),
      sec("fdy-cta", "footer-cta", { x: 0, y: 38, w: 12, h: 6 }, {
        heading: "Open a trade account",
        body: "Dealer pricing, credit terms and a named manager who answers the phone.",
        tone: "accent",
      }),
    ]),
    page("fdy-catalogue", "/catalogue", "Catalogue", [
      sec("fdy-cat-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Catalogue",
        heading: "Every line we hold, with the stock position",
        body: "Filter by category on the left. Prices are dealer prices, exclusive of GST.",
        ctaLabel: "",
        secondaryLabel: "",
        tone: "surface",
      }),
      sec("fdy-cat-nav", "category-tiles", { x: 0, y: 6, w: 3, h: 14 }, {
        heading: "Categories",
        style: "list",
        columns: 1,
        limit: 10,
      }),
      sec("fdy-cat-grid", "product-grid", { x: 3, y: 6, w: 9, h: 14 }, {
        heading: "All stocked lines",
        source: "all",
        limit: 12,
        columns: 3,
      }),
      sec("fdy-cat-cta", "footer-cta", { x: 0, y: 20, w: 12, h: 5 }, {
        heading: "Need a line we don't list?",
        body: "Send the drawing or the part number. We quote against the specification, not the category.",
        ctaLabel: "Send a specification",
        tone: "contrast",
      }),
    ]),
    page("fdy-product", "/product", "Line detail", [
      sec("fdy-prd-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Fasteners & fixings",
        heading: "Hex bolt M12 × 60, Grade 8.8",
        body: "Hot-dip galvanised, full thread, supplied to IS 1367 with a batch certificate.",
        ctaLabel: "Add to enquiry",
        secondaryLabel: "Download the datasheet",
        tone: "surface",
      }),
      sec("fdy-prd-image", "image-with-text", { x: 0, y: 6, w: 7, h: 8 }, {
        heading: "Coated, counted, cartoned",
        body: "Galvanising is done in-house to a 45-micron minimum and every carton is weight-counted before it is sealed, so a 500-piece box is 500 pieces.",
        caption: "Ludhiana plating line",
        imageSide: "left",
      }),
      sec("fdy-prd-enquiry", "enquiry-form", { x: 7, y: 6, w: 5, h: 12 }, {
        heading: "Ask for a quote on this line",
        body: "Give us the grade, the finish and the quantity. Same-day reply on working days.",
      }),
      sec("fdy-prd-prices", "price-table", { x: 0, y: 14, w: 7, h: 10 }, {
        heading: "Related sizes and prices",
        source: "category:fasteners",
        limit: 6,
      }),
      sec("fdy-prd-faq", "faq", { x: 7, y: 18, w: 5, h: 9 }, {
        heading: "Ordering this line",
        q1: "Can I mix sizes to reach the MOQ?",
        a1: "Yes, within the same grade and finish. Mixed cartons are labelled per size.",
        q2: "Do you supply to drawing?",
        a2: "For runs above 5,000 pieces, yes. Send the drawing and we will come back with a tooling position.",
        q3: "Is the certificate per batch or per order?",
        a3: "Per batch. An order drawn from two batches carries both certificates.",
      }),
      sec("fdy-prd-carousel", "product-carousel", { x: 0, y: 27, w: 12, h: 8 }, {
        heading: "Bought together",
        source: "category:tools",
        limit: 8,
      }),
      sec("fdy-prd-cta", "footer-cta", { x: 0, y: 35, w: 12, h: 5 }, {
        heading: "Bulk requirement?",
        body: "Above ten tonnes a year we will hold a rate for the contract period.",
        ctaLabel: "Start a rate contract",
        tone: "accent",
      }),
    ]),
  ],
};
