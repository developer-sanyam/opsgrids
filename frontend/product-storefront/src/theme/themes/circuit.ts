/**
 * Circuit — electrical components.
 *
 * The only dark theme in the set: near-black ground, a monospaced heading face, and a lime accent
 * used as a signal rather than decoration. Composition is spec-first — a carousel of switchgear
 * directly under the hero, then a category rail beside a tight grid — because a panel builder
 * shops by rating, not by category page.
 */

import type { Theme } from "../schema";
import { page, sec } from "./build";

export const circuit: Theme = {
  key: "circuit",
  name: "Circuit",
  description:
    "Dark, monospaced and spec-led. A lime signal accent, a rail of categories beside a tight grid, and ratings before pictures.",
  industry: "Electrical components",
  mode: "trade",
  tokens: {
    colorBg: "#0B0E11",
    colorSurface: "#151B21",
    colorText: "#EDF2F5",
    colorMuted: "#9AA8B3",
    colorAccent: "#C7F03A",
    colorAccentText: "#10160A",
    colorBorder: "#26313A",
    fontHeading: 'ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    fontBody: 'system-ui, "Segoe UI", Roboto, sans-serif',
    radius: 4,
    space: 16,
    maxWidth: 1280,
  },
  pages: [
    page("cir-home", "/", "Home", [
      sec("cir-hero", "hero", { x: 0, y: 0, w: 12, h: 9 }, {
        eyebrow: "Panel builders & electrical contractors",
        heading: "Rated, stocked, and on the shelf by rating",
        body: "Breakers, contactors and cable from the standard list every panel drawing already names.",
        ctaLabel: "Browse by rating",
        secondaryLabel: "Send a bill of materials",
        tone: "surface",
        align: "left",
        image: "/img/hero-circuit.svg",
      }),
      sec("cir-carousel", "product-carousel", { x: 0, y: 9, w: 12, h: 8 }, {
        heading: "Switchgear on the shelf",
        source: "category:switchgear",
        limit: 8,
        ctaLabel: "All switchgear",
      }),
      sec("cir-rail", "category-tiles", { x: 0, y: 17, w: 4, h: 10 }, {
        heading: "Categories",
        style: "list",
        columns: 1,
        limit: 6,
      }),
      sec("cir-grid", "product-grid", { x: 4, y: 17, w: 8, h: 10 }, {
        heading: "Cable and glands",
        source: "category:cables",
        limit: 6,
        columns: 3,
        priceNote: "exclusive",
      }),
      sec("cir-features", "feature-columns", { x: 0, y: 27, w: 12, h: 6 }, {
        heading: "What a BOM gets you",
        columns: 3,
        item1Title: "Priced in four hours",
        item1Body: "Send a bill of materials in any format. We return it priced, with substitutions flagged, not swapped.",
        item2Title: "Kitted per panel",
        item2Body: "Multi-panel orders arrive kitted and labelled by panel reference, so the shop floor does not sort.",
        item3Title: "Type-test papers",
        item3Body: "Type-test certificates for every switchgear line, current revision, in the consignment.",
      }),
      sec("cir-faq", "faq", { x: 0, y: 33, w: 7, h: 9 }, {
        heading: "Specification questions",
        q1: "Will you substitute a make without asking?",
        a1: "No. A substitution is offered in writing with the datasheet, and we wait for your approval.",
        q2: "Do you stock 10kA and 25kA in the same range?",
        a2: "Both, in single and triple pole. The rating is on the card, not in a datasheet you have to open.",
        q3: "Can you hold stock against a panel programme?",
        a3: "Yes, for programmes over ₹5 lakh. We allocate against the schedule and release per panel.",
      }),
      sec("cir-enquiry", "enquiry-form", { x: 7, y: 33, w: 5, h: 12 }, {
        heading: "Send a bill of materials",
        body: "Paste it, list it, or describe the panel. Priced within four working hours.",
        submitLabel: "Send the BOM",
        successMessage: "BOM received. You will get it back priced within four working hours.",
      }),
      sec("cir-cta", "footer-cta", { x: 0, y: 45, w: 12, h: 5 }, {
        heading: "Panel shop account",
        body: "Kitting, labelling and net 30, on one account number.",
        ctaLabel: "Open an account",
        tone: "accent",
      }),
    ]),
    page("cir-catalogue", "/catalogue", "Catalogue", [
      sec("cir-cat-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Catalogue",
        heading: "Everything, by rating and make",
        body: "Prices are trade prices, exclusive of GST. Availability is the live warehouse position.",
        ctaLabel: "",
        secondaryLabel: "",
        tone: "surface",
      }),
      sec("cir-cat-table", "price-table", { x: 0, y: 6, w: 12, h: 12 }, {
        heading: "Trade price list",
        source: "all",
        limit: 10,
        note: "Trade prices. Project rates are quoted against a bill of materials.",
      }),
      sec("cir-cat-grid", "product-grid", { x: 0, y: 18, w: 12, h: 11 }, {
        heading: "Cable and accessories",
        source: "category:cables",
        limit: 8,
        columns: 4,
      }),
      sec("cir-cat-cta", "footer-cta", { x: 0, y: 29, w: 12, h: 5 }, {
        heading: "Not on the list?",
        body: "We source to specification from eleven principals. Ask before you compromise the drawing.",
        ctaLabel: "Ask about a line",
        tone: "contrast",
      }),
    ]),
    page("cir-product", "/product", "Line detail", [
      sec("cir-prd-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Switchgear & protection",
        heading: "MCB 32A, C-curve, single pole",
        body: "10kA breaking capacity, 240V AC, DIN rail mount. Type-test papers current to 2026.",
        ctaLabel: "Add to the BOM",
        secondaryLabel: "Datasheet",
        tone: "surface",
      }),
      sec("cir-prd-plate", "image-with-text", { x: 0, y: 6, w: 6, h: 9 }, {
        heading: "Stocked across the whole curve set",
        body: "B, C and D curves from 6A to 63A, single through four pole, in one bin system. The rating you need is almost never the one we have to order.",
        caption: "Chennai bin store",
        imageSide: "left",
        image: "/img/hero-circuit.svg",
      }),
      sec("cir-prd-prices", "price-table", { x: 6, y: 6, w: 6, h: 9 }, {
        heading: "Ratings and rates",
        source: "category:switchgear",
        limit: 4,
        showUnit: false,
      }),
      sec("cir-prd-faq", "faq", { x: 0, y: 15, w: 6, h: 9 }, {
        heading: "Fitting and compliance",
        q1: "Are these suitable for a residential DB?",
        a1: "Yes. The same range covers residential and light commercial; the breaking capacity is what changes.",
        q2: "Do you supply the matching DB?",
        a2: "Four to eighteen way, single and triple phase, from the same stock.",
        q3: "What is the warranty position?",
        a3: "Two years from invoice, handled by us rather than by the principal.",
      }),
      sec("cir-prd-contact", "contact-block", { x: 6, y: 15, w: 6, h: 9 }, {
        heading: "Technical desk",
        address: "No. 44, Ambattur Industrial Estate\nChennai, Tamil Nadu 600058",
        phone: "+91 44 4285 9000",
        email: "technical@example.co.in",
      }),
      sec("cir-prd-carousel", "product-carousel", { x: 0, y: 24, w: 12, h: 8 }, {
        heading: "Usually ordered with",
        source: "in-stock",
        limit: 8,
      }),
      sec("cir-prd-cta", "footer-cta", { x: 0, y: 32, w: 12, h: 5 }, {
        heading: "Building a panel?",
        body: "Send the single-line diagram and we will price the whole board.",
        ctaLabel: "Price my board",
        tone: "accent",
      }),
    ]),
  ],
};
