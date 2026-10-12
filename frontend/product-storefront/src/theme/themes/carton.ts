/**
 * Carton — packaging and despatch consumables.
 *
 * Kraft-paper ground, the softest radius in the set, and a carousel directly under a centred
 * accent hero. Packaging is a repeat-purchase category, so the composition is built around
 * re-ordering quickly: the row of what moved last week comes before any explanation.
 */

import type { Theme } from "../schema";
import { page, sec } from "./build";

export const carton: Theme = {
  key: "carton",
  name: "Carton",
  description:
    "Kraft tones and soft corners, built for re-ordering. A carousel of last week's lines sits above the story.",
  industry: "Packaging & consumables",
  mode: "trade",
  tokens: {
    colorBg: "#EFE7DA",
    colorSurface: "#FFFCF7",
    colorText: "#2B231A",
    colorMuted: "#6B5E4E",
    colorAccent: "#9C4A1A",
    colorAccentText: "#FFFFFF",
    colorBorder: "#DCCFBB",
    fontHeading: '"Trebuchet MS", "Segoe UI", system-ui, sans-serif',
    fontBody: 'system-ui, "Segoe UI", Roboto, sans-serif',
    radius: 18,
    space: 16,
    maxWidth: 1200,
  },
  pages: [
    page("crt-home", "/", "Home", [
      sec("crt-hero", "hero", { x: 0, y: 0, w: 12, h: 9 }, {
        eyebrow: "Corrugation, strapping and tape",
        heading: "Re-order in a minute, dispatch in a day",
        body: "Cartons, strapping and consumables for a despatch bay that cannot afford to stop.",
        ctaLabel: "Re-order your last list",
        secondaryLabel: "See the full range",
        tone: "accent",
        align: "center",
        image: "/img/hero-carton.svg",
        /* Words over the picture, not beside it. `Hero` only paints its plate when the copy is
           left-aligned (`showPlate = !centred && !stacked`), so a centred hero with an `image`
           set silently shows nothing. The centred composition this theme is described by is kept
           — the picture moves behind the words instead of being dropped. */
        layout: "overlay",
        overlay: "scrim",
        minHeight: 380,
      }),
      sec("crt-carousel", "product-carousel", { x: 0, y: 9, w: 12, h: 8 }, {
        heading: "What you ordered last",
        source: "category:packaging",
        limit: 8,
        ctaLabel: "Full range",
      }),
      sec("crt-plate", "image-with-text", { x: 0, y: 17, w: 12, h: 8 }, {
        heading: "Printed, creased and delivered flat",
        body: "Two-colour flexo printing in-house on runs from 500 pieces. Flat-packed on a pallet that fits a standard bay, so the first thing your team does with a delivery is not rearrange it.",
        caption: "Corrugation line, Vasai",
        imageSide: "right",
        ctaLabel: "Ask about printing",
        image: "/img/plate-carton.svg",
      }),
      sec("crt-features", "feature-columns", { x: 0, y: 25, w: 12, h: 6 }, {
        heading: "Why despatch managers switch",
        columns: 3,
        item1Title: "Standing schedules",
        item1Body: "Weekly or fortnightly releases against one purchase order, invoiced monthly.",
        item2Title: "Burst-strength tested",
        item2Body: "Every batch is tested and the figure is printed on the carton, not just on the invoice.",
        item3Title: "Buffer stock held",
        item3Body: "We hold four weeks of your specification so a spike does not become a stockout.",
      }),
      sec("crt-quotes", "testimonials", { x: 0, y: 31, w: 12, h: 7 }, {
        heading: "From the despatch bay",
        quote1: "[Your customer's words, quoted with their permission]",
        author1: "[Customer name]",
        role1: "[Role, company]",
        quote2: "[Your customer's words, quoted with their permission]",
        author2: "[Customer name]",
        role2: "[Role, company]",
      }),
      sec("crt-logos", "logo-wall", { x: 0, y: 38, w: 12, h: 4 }, {
        heading: "Packing for",
        names: "[Client name 1]\n[Client name 2]\n[Client name 3]\n[Client name 4]",
      }),
      sec("crt-cta", "footer-cta", { x: 0, y: 42, w: 12, h: 6 }, {
        heading: "Set up a standing schedule",
        body: "One purchase order, weekly releases, monthly invoice.",
        ctaLabel: "Set up a schedule",
        tone: "contrast",
      }),
    ]),
    page("crt-catalogue", "/catalogue", "Catalogue", [
      sec("crt-cat-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Catalogue",
        heading: "Everything the bay gets through",
        body: "Cartons, strapping, tape, void fill and labels. Case quantities, case prices.",
        ctaLabel: "",
        secondaryLabel: "",
        tone: "surface",
        align: "center",
      }),
      sec("crt-cat-tiles", "category-tiles", { x: 0, y: 6, w: 12, h: 6 }, {
        heading: "By use",
        columns: 4,
        limit: 8,
        // Two across on a phone: these are word-only tiles carrying long category names.
        mobileColumns: 2,
      }),
      sec("crt-cat-grid", "product-grid", { x: 0, y: 12, w: 12, h: 13 }, {
        heading: "All lines",
        source: "all",
        limit: 12,
        columns: 4,
      }),
      sec("crt-cat-enquiry", "enquiry-form", { x: 0, y: 25, w: 6, h: 12 }, {
        storeSlug: "carton",
        heading: "Ask for a sample",
        body: "Tell us the product you pack and we will send two sizes to test.",
        submitLabel: "Request samples",
        successMessage: "Sample request received. Two sizes will reach you within four working days.",
        showQuantity: false,
      }),
      sec("crt-cat-faq", "faq", { x: 6, y: 25, w: 6, h: 9 }, {
        heading: "Before you order",
        q1: "What is the minimum print run?",
        a1: "500 pieces for two-colour flexo, 2,000 for four-colour. Plain stock has no minimum beyond the case.",
        q2: "Can you match an existing carton?",
        a2: "Send one. We will measure it, match the board grade and quote against the sample.",
        q3: "Do you take back offcuts?",
        a3: "Yes, on a scheduled collection. It is credited by weight against the next invoice.",
      }),
      sec("crt-cat-cta", "footer-cta", { x: 0, y: 37, w: 12, h: 6 }, {
        heading: "New line launching?",
        body: "Give us the product dimensions and we will design the pack around it.",
        ctaLabel: "Start a pack design",
        tone: "accent",
      }),
    ]),
    page("crt-product", "/product", "Line detail", [
      sec("crt-prd-hero", "hero", { x: 0, y: 0, w: 12, h: 7 }, {
        eyebrow: "Packaging & consumables",
        heading: "Corrugated carton, 5-ply",
        body: "450 × 300 × 300mm, 7kg bursting strength, delivered flat in cases of fifty.",
        ctaLabel: "Add to the order",
        secondaryLabel: "Request a sample",
        tone: "surface",
      }),
      sec("crt-prd-plate", "image-with-text", { x: 0, y: 7, w: 7, h: 9 }, {
        heading: "Board grade you can verify",
        body: "Every case carries its batch and its tested bursting strength. If a load reads under specification, it goes back and the replacement is free.",
        caption: "Board testing, Vasai",
        imageSide: "left",
        image: "/img/plate-carton.svg",
      }),
      sec("crt-prd-enquiry", "enquiry-form", { x: 7, y: 7, w: 5, h: 12 }, {
        storeSlug: "carton",
        heading: "Order or ask",
        body: "Quantities, print, or a size we do not list — all three go in the same box below.",
      }),
      sec("crt-prd-prices", "price-table", { x: 0, y: 16, w: 7, h: 10 }, {
        heading: "Sizes and case prices",
        source: "category:packaging",
        limit: 5,
        note: "Case prices. Printed stock is quoted separately against artwork.",
      }),
      sec("crt-prd-grid", "product-grid", { x: 0, y: 28, w: 12, h: 11 }, {
        heading: "Goes with",
        source: "in-stock",
        limit: 8,
        columns: 4,
      }),
      sec("crt-prd-cta", "footer-cta", { x: 0, y: 39, w: 12, h: 6 }, {
        heading: "Packing something unusual?",
        body: "Send the dimensions and the weight. We will come back with two options and a price.",
        ctaLabel: "Ask the design desk",
        tone: "accent",
      }),
    ]),
  ],
};
