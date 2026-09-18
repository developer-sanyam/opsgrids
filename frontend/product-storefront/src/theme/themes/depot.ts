/**
 * Depot — wholesale distribution.
 *
 * The densest theme in the set: a centred accent hero, four value columns, and a price list
 * sitting above the fold with the enquiry form beside it. A distributor's buyer arrives knowing
 * the part; what they want is the rate and the MOQ, so the price table outranks the product grid.
 */

import type { Theme } from "../schema";
import { page, sec } from "./build";

export const depot: Theme = {
  key: "depot",
  name: "Depot",
  description:
    "Price-list first. A centred blue hero, four value columns, and the rate card above the fold beside a live enquiry form.",
  industry: "Wholesale distribution",
  mode: "trade",
  tokens: {
    colorBg: "#FFFFFF",
    colorSurface: "#F6F8FA",
    colorText: "#101418",
    colorMuted: "#525C68",
    colorAccent: "#0B57D0",
    colorAccentText: "#FFFFFF",
    colorBorder: "#DCE2E9",
    fontHeading: 'Manrope, system-ui, "Segoe UI", sans-serif',
    fontBody: 'Manrope, system-ui, "Segoe UI", sans-serif',
    radius: 6,
    space: 14,
    maxWidth: 1320,
  },
  pages: [
    page("dpt-home", "/", "Home", [
      sec("dpt-hero", "hero", { x: 0, y: 0, w: 12, h: 8 }, {
        eyebrow: "Regional distributor · West & South",
        heading: "One order, one invoice, eleven categories",
        body: "Mixed pallets built to your line list and dispatched as a single consignment.",
        ctaLabel: "See the rate card",
        ctaHref: "/store/catalogue",
        secondaryLabel: "Open a trade account",
        tone: "accent",
        align: "center",
        image: "/img/hero-depot.svg",
        /* Still the centred blue hero this theme is described by — the picture sits behind the
           words rather than beside them, because `Hero` only paints its split plate when the copy
           is left-aligned (`showPlate = !centred && !stacked`) and otherwise drops the `image`
           without saying so. The scrim keeps the blue; `hero-depot` is drawn dark so the white
           copy over it stays well clear of 4.5:1. */
        layout: "overlay",
        overlay: "scrim",
        minHeight: 360,
      }),
      sec("dpt-features", "feature-columns", { x: 0, y: 8, w: 12, h: 6 }, {
        heading: "What a distributor account gets you",
        columns: 4,
        item1Title: "Slab pricing",
        item1Body: "Four price slabs per line, published. No negotiation required for a standard order.",
        item2Title: "Mixed pallets",
        item2Body: "Any combination across categories consolidates into one despatch and one invoice.",
        item3Title: "Stock visibility",
        item3Body: "The availability on this site is the warehouse position, refreshed every hour.",
        item4Title: "Returns window",
        item4Body: "Thirty days on unopened full cases, credited against the next order.",
      }),
      sec("dpt-prices", "price-table", { x: 0, y: 14, w: 8, h: 11 }, {
        heading: "This month's rate card",
        source: "all",
        limit: 8,
        note: "Slab pricing applies above 10× MOQ. Freight at actuals. Rates hold to the end of the month.",
      }),
      sec("dpt-enquiry", "enquiry-form", { x: 8, y: 14, w: 4, h: 13 }, {
        heading: "Send your line list",
        body: "Paste the list and the quantities. We price it and come back the same working day.",
        submitLabel: "Get it priced",
        successMessage: "List received. A regional manager will reply to the email you gave us.",
      }),
      sec("dpt-carousel", "product-carousel", { x: 0, y: 27, w: 12, h: 8 }, {
        heading: "Moving this week",
        source: "in-stock",
        limit: 10,
      }),
      sec("dpt-logos", "logo-wall", { x: 0, y: 35, w: 12, h: 4 }, {
        heading: "Supplying",
        names: "Shapoorji Pallonji\nL&T Construction\nTata Projects\nAdani Infra\nNCC Limited\nAfcons",
      }),
      sec("dpt-cta", "footer-cta", { x: 0, y: 39, w: 12, h: 5 }, {
        heading: "Ready to place a standing order?",
        body: "Set the schedule once and we release against it every month.",
        ctaLabel: "Set up a standing order",
        tone: "contrast",
      }),
    ]),
    page("dpt-catalogue", "/catalogue", "Catalogue", [
      sec("dpt-cat-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Catalogue",
        heading: "Eleven categories, one despatch bay",
        body: "Everything below ships together. Availability is the live warehouse position.",
        ctaLabel: "",
        secondaryLabel: "",
        tone: "surface",
        align: "center",
      }),
      sec("dpt-cat-grid", "product-grid", { x: 0, y: 6, w: 12, h: 13 }, {
        heading: "All lines",
        source: "all",
        limit: 12,
        columns: 4,
      }),
      sec("dpt-cat-tiles", "category-tiles", { x: 0, y: 19, w: 12, h: 6 }, {
        heading: "Narrow it down",
        columns: 4,
        limit: 8,
        // Two across on a phone: these are word-only tiles carrying long category names.
        mobileColumns: 2,
      }),
      sec("dpt-cat-cta", "footer-cta", { x: 0, y: 25, w: 12, h: 5 }, {
        heading: "Can't find a line?",
        body: "If we don't stock it, we will tell you who does. That is usually the fastest answer.",
        ctaLabel: "Ask the desk",
        tone: "accent",
      }),
    ]),
    page("dpt-product", "/product", "Line detail", [
      sec("dpt-prd-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Cables & wiring",
        heading: "FRLS single core, 2.5 sq mm",
        body: "1100V grade, 90-metre coils, flame retardant low smoke. Stocked in six colours.",
        ctaLabel: "Add to the line list",
        secondaryLabel: "",
        tone: "surface",
      }),
      sec("dpt-prd-prices", "price-table", { x: 0, y: 6, w: 12, h: 11 }, {
        heading: "Slab pricing across the range",
        source: "category:cables",
        limit: 6,
        note: "Slabs are per despatch, not per month. Cut lengths carry a ₹250 handling charge.",
      }),
      sec("dpt-prd-image", "image-with-text", { x: 0, y: 17, w: 6, h: 8 }, {
        heading: "Cut, tagged and coiled to order",
        body: "Any length above 20 metres is cut, tagged with the order reference and re-coiled, so a site store can put it away without measuring it again.",
        caption: "Bhiwandi cutting bay",
        imageSide: "right",
        image: "/img/hero-depot.svg",
      }),
      sec("dpt-prd-contact", "contact-block", { x: 6, y: 17, w: 6, h: 8 }, {
        heading: "The cable desk",
        phone: "+91 22 4000 1182",
        email: "cable.desk@example.co.in",
      }),
      sec("dpt-prd-cta", "footer-cta", { x: 0, y: 25, w: 12, h: 5 }, {
        heading: "Project quantities?",
        body: "Above 50 coils we quote ex-mill with a delivery schedule against your programme.",
        ctaLabel: "Request a project rate",
        tone: "accent",
      }),
    ]),
  ],
};
