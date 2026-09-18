/**
 * QuickCart — quick-commerce grocery.
 *
 * The first `retail` theme, and the one composed against the phone: white ground, the roundest
 * corners and the tightest spacing step in the set, a grid of four category tiles across, and a
 * long run of horizontally swiped rails — one per aisle — that a thumb walks down.
 *
 * **Why the accent is green and the brand yellow is a tint.** A quick-commerce marigold on white
 * measures about 1.6:1, and three places in the renderer paint `colorAccent` as *text* on
 * `colorBg`: the hero's eyebrow, the active navigation link and a category tile's line count. A
 * yellow accent would fail all three. So the accent token is a green that clears 4.9:1 on both the
 * page and the surface, and the marigold lands where it belongs — as a **ground** on the promo
 * banner, the collection tiles and the category panels, where the theme's own text on it measures
 * 11.95:1. That is also the honest reading of the reference: the yellow there is a brand surface,
 * never a link colour.
 *
 * **Every source names a category.** The fixture catalogue for a retail store carries the grocery
 * aisles *and* the furniture showroom, so `all`, `featured` and `in-stock` would put a hand-carved
 * rosewood dining table in a rail of atta. Each rail points at one aisle, and no two repeat.
 */

import type { Theme } from "../schema";
import { page, sec } from "./build";

/**
 * The brand marigold.
 *
 * A ground, not a foreground: it is only ever handed to a `tint` setting, where the renderer pairs
 * it with `colorText` (11.95:1) and never with `colorAccentText`. It is deliberately not a token —
 * the twelve tokens are all used as colours *of something*, and this is the colour *under*
 * something.
 */
const MARIGOLD = "#F7CB45";

/**
 * The delivery promise and the address, which every page repeats.
 *
 * Spread into each bar rather than retyped: a store that promises eleven minutes on the home page
 * and twelve on the catalogue is a store nobody believes, and three copies of the same three
 * strings is how that happens. The search placeholder stays per-page — it is the one line that
 * should read differently on a line-detail page.
 */
const BAR = {
  promiseText: "Delivery in 11 minutes",
  locationLabel: "Deliver to",
  locationText: "Sakinaka, Andheri East, Mumbai 400072",
  /* No account link: this store has three routes and none of them is an account page. A control
     that looks like a destination and goes nowhere costs a buyer more than the control is worth. */
  accountLabel: "",
};

/**
 * The footer, which is identical on all three routes.
 *
 * Written once for the same reason as the bar. Every link points at one of the store's three real
 * routes; the opening hours and the phone number are lines with no target, which the link-list
 * parser renders as plain text rather than as a link to nowhere.
 */
const FOOTER = {
  aboutTitle: "About QuickCart",
  aboutText:
    "Eleven dark stores across Mumbai, Thane and Navi Mumbai, each holding the two thousand lines a household actually reorders.",
  col1Title: "Shop",
  col1Links:
    "Atta, Rice & Dal | /store/catalogue?category=staples\nDairy, Bread & Eggs | /store/catalogue?category=dairy\nSnacks & Namkeen | /store/catalogue?category=snacks\nFruits & Vegetables | /store/catalogue?category=fresh",
  col2Title: "Every day",
  col2Links:
    "Tea, Coffee & Drinks | /store/catalogue?category=beverages\nCleaning & Household | /store/catalogue?category=household\nToday's offers | /store/catalogue\nSample line | /store/product",
  col3Title: "QuickCart",
  col3Links: "The catalogue | /store/catalogue\nHow delivery works | /store/product\nHome | /store",
  col4Title: "Talk to us",
  col4Links:
    "+91 22 4000 1180 | tel:+912240001180\nhelp@example.co.in | mailto:help@example.co.in\nEvery day, 6am to midnight",
  socialTitle: "Follow",
  socialLinks: "Instagram | https://instagram.com\nX | https://x.com",
  paymentTitle: "We accept",
  paymentMethods: "UPI\nVisa\nMastercard\nRuPay\nNet banking\nCash on delivery",
  legalText: "© 2026 QuickCart Retail Pvt Ltd · GSTIN 27AABCQ0000Q1ZR · All prices in INR, inclusive of GST",
  columns: 4,
};

export const quickcart: Theme = {
  key: "quickcart",
  name: "QuickCart",
  description:
    "Quick-commerce grocery, designed at phone width. A delivery promise above everything, four category tiles across, and a rail per aisle with ADD on every card.",
  industry: "Grocery & quick commerce",
  mode: "retail",
  tokens: {
    colorBg: "#FFFFFF",
    colorSurface: "#F7F8FA",
    colorText: "#14161A",
    colorMuted: "#5C6470",
    /* Green, not the brand yellow — see the note at the top of this file. 5.49:1 on the page,
       measured by theme/contrast.test.ts. The brand marigold would have been 1.55:1 and fails
       every place the renderer paints the accent as text. */
    colorAccent: "#0B7A24",
    colorAccentText: "#FFFFFF",
    colorBorder: "#E6E9EE",
    /* Nunito Sans if a device happens to have it; otherwise the platform's own UI face, which is
       what a grocery app reads like on that platform. Nothing here is loaded over the network —
       this app ships no webfont loader, so a face that is not installed silently falls through. */
    fontHeading:
      '"Nunito Sans", "Segoe UI Variable Display", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif',
    fontBody:
      '"Nunito Sans", "Segoe UI Variable Text", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif',
    radius: 14,
    space: 12,
    maxWidth: 1180,
  },
  pages: [
    page("qc-home", "/", "Home", [
      sec("qc-bar", "announcement-bar", { x: 0, y: 0, w: 12, h: 2 }, {
        ...BAR,
        searchPlaceholder: 'Search for "atta"',
      }),
      sec("qc-hero", "hero", { x: 0, y: 2, w: 12, h: 8 }, {
        eyebrow: "Now serving Mumbai, Thane and Navi Mumbai",
        heading: "The weekly shop, at the door before the kettle boils",
        body: "Two thousand everyday lines from the brands your kitchen already runs on, picked and packed in minutes.",
        ctaLabel: "Start shopping",
        ctaHref: "/store/catalogue",
        secondaryLabel: "See what's in stock",
        secondaryHref: "/store/catalogue",
        image: "/img/cat-staples.svg",
        tone: "surface",
        align: "left",
      }),
      sec("qc-cats", "category-tiles", { x: 0, y: 10, w: 12, h: 6 }, {
        heading: "Shop by category",
        style: "compact",
        aspect: "square",
        columns: 6,
        mobileColumns: 4,
        limit: 6,
        showBlurb: false,
        showCount: true,
        tint: MARIGOLD,
      }),
      sec("qc-rail-staples", "product-carousel", { x: 0, y: 16, w: 12, h: 7 }, {
        heading: "Atta, rice and dal",
        source: "category:staples",
        limit: 8,
        ctaLabel: "See all",
        ctaHref: "/store/catalogue",
        ctaPlacement: "trailing",
        cardWidth: 200,
        cardStyle: "compact",
        priceNote: "inclusive",
      }),
      sec("qc-stores", "collection-row", { x: 0, y: 23, w: 12, h: 5 }, {
        heading: "Shop by store",
        item1Title: "Grocery & Kitchen",
        item1Image: "/img/cat-staples.svg",
        item1Href: "/store/catalogue?category=staples",
        item1Tint: MARIGOLD,
        item2Title: "Snacks & Drinks",
        item2Image: "/img/cat-snacks.svg",
        item2Href: "/store/catalogue?category=snacks",
        item2Tint: MARIGOLD,
        item3Title: "Home & Cleaning",
        item3Image: "/img/cat-household.svg",
        item3Href: "/store/catalogue?category=household",
        item3Tint: MARIGOLD,
        item4Title: "Fruit & Vegetables",
        item4Image: "/img/cat-fresh.svg",
        item4Href: "/store/catalogue?category=fresh",
        item4Tint: MARIGOLD,
        columns: 4,
        mobileColumns: 2,
        aspect: "wide",
        // A swipeable row on a phone: the fourth tile peeks, so it is obvious there is more.
        scroll: true,
      }),
      sec("qc-rail-dairy", "product-carousel", { x: 0, y: 28, w: 12, h: 7 }, {
        heading: "Dairy, bread and eggs",
        source: "category:dairy",
        limit: 8,
        ctaLabel: "See all",
        ctaHref: "/store/catalogue",
        cardWidth: 200,
        cardStyle: "compact",
        priceNote: "inclusive",
      }),
      sec("qc-rail-snacks", "product-carousel", { x: 0, y: 35, w: 12, h: 7 }, {
        heading: "Snacks and namkeen",
        source: "category:snacks",
        limit: 8,
        ctaLabel: "See all",
        ctaHref: "/store/catalogue",
        cardWidth: 200,
        cardStyle: "compact",
        priceNote: "inclusive",
      }),
      sec("qc-promo", "promo-banner", { x: 0, y: 42, w: 12, h: 8 }, {
        eyebrow: "This week",
        heading: "Monsoon essentials, delivered",
        body: "Chai, biscuits and everything else that makes a wet Tuesday bearable.",
        tint: MARIGOLD,
        image: "/img/cat-beverages.svg",
        ctaLabel: "Shop the edit",
        ctaHref: "/store/catalogue",
        tile1Title: "Hot drinks",
        tile1Image: "/img/p-tea.svg",
        tile1Href: "/store/catalogue?category=beverages",
        tile2Title: "Biscuits",
        tile2Image: "/img/p-biscuits.svg",
        tile2Href: "/store/catalogue?category=snacks",
        tile3Title: "Namkeen",
        tile3Image: "/img/p-namkeen.svg",
        tile3Href: "/store/catalogue?category=snacks",
        tile4Title: "Instant coffee",
        tile4Image: "/img/p-coffee.svg",
        tile4Href: "/store/catalogue?category=beverages",
        bleed: true,
      }),
      sec("qc-rail-beverages", "product-carousel", { x: 0, y: 50, w: 12, h: 7 }, {
        heading: "Tea, coffee and cold drinks",
        source: "category:beverages",
        limit: 8,
        ctaLabel: "See all",
        ctaHref: "/store/catalogue",
        cardWidth: 200,
        cardStyle: "compact",
        priceNote: "inclusive",
      }),
      sec("qc-rail-fresh", "product-carousel", { x: 0, y: 57, w: 12, h: 7 }, {
        heading: "Fruit and vegetables",
        source: "category:fresh",
        limit: 8,
        ctaLabel: "See all",
        ctaHref: "/store/catalogue",
        cardWidth: 200,
        cardStyle: "compact",
        priceNote: "inclusive",
      }),
      sec("qc-wordmark", "rich-text", { x: 0, y: 64, w: 12, h: 2 }, {
        heading: "quickcart",
        body: "Eleven minutes, most days. Eighteen on a bad one, and we will say so before you pay.",
        size: "display",
        align: "center",
        tone: "none",
      }),
      sec("qc-footer", "footer-columns", { x: 0, y: 66, w: 12, h: 6 }, {
        ...FOOTER,
      }),
    ]),

    page("qc-catalogue", "/catalogue", "Catalogue", [
      sec("qc-cat-bar", "announcement-bar", { x: 0, y: 0, w: 12, h: 2 }, {
        ...BAR,
        searchPlaceholder: 'Search for "atta"',
      }),
      sec("qc-cat-hero", "hero", { x: 0, y: 2, w: 12, h: 8 }, {
        eyebrow: "Catalogue",
        heading: "Everything on the shelf today",
        body: "Prices are what you pay at the door, inclusive of GST. Stock is the live position at your nearest store.",
        ctaLabel: "",
        secondaryLabel: "",
        image: "/img/cat-household.svg",
        tone: "surface",
      }),
      sec("qc-cat-tiles", "category-tiles", { x: 0, y: 10, w: 12, h: 6 }, {
        heading: "Jump to an aisle",
        style: "compact",
        aspect: "square",
        columns: 6,
        mobileColumns: 4,
        limit: 6,
        showBlurb: false,
        tint: MARIGOLD,
      }),
      sec("qc-cat-staples", "product-grid", { x: 0, y: 16, w: 12, h: 16 }, {
        heading: "Atta, rice and dal",
        source: "category:staples",
        limit: 8,
        columns: 4,
        mobileColumns: 2,
        showCode: false,
        showStock: true,
        priceNote: "inclusive",
        cardStyle: "detail",
      }),
      sec("qc-cat-household", "product-grid", { x: 0, y: 32, w: 12, h: 15 }, {
        heading: "Cleaning and household",
        source: "category:household",
        limit: 8,
        columns: 4,
        mobileColumns: 2,
        showCode: false,
        showStock: true,
        priceNote: "inclusive",
        cardStyle: "detail",
      }),
      sec("qc-cat-rail", "product-carousel", { x: 0, y: 47, w: 12, h: 7 }, {
        heading: "Snacks and namkeen",
        source: "category:snacks",
        limit: 8,
        ctaLabel: "See all",
        ctaHref: "/store/catalogue",
        cardWidth: 200,
        cardStyle: "compact",
        priceNote: "inclusive",
      }),
      sec("qc-cat-footer", "footer-columns", { x: 0, y: 54, w: 12, h: 6 }, {
        ...FOOTER,
      }),
    ]),

    page("qc-product", "/product", "Line detail", [
      sec("qc-prd-bar", "announcement-bar", { x: 0, y: 0, w: 12, h: 2 }, {
        ...BAR,
        searchPlaceholder: 'Search for "milk"',
      }),
      sec("qc-prd-hero", "hero", { x: 0, y: 2, w: 12, h: 8 }, {
        eyebrow: "Dairy, bread and eggs",
        heading: "Amul Taaza Toned Milk",
        body: "Homogenised toned milk, 3% fat. Kept at 4°C from the dairy to the door, and dated the morning it reaches you.",
        ctaLabel: "Shop all dairy",
        ctaHref: "/store/catalogue",
        secondaryLabel: "See today's offers",
        secondaryHref: "/store/catalogue",
        image: "/img/p-milk.svg",
        tone: "surface",
        align: "left",
      }),
      sec("qc-prd-band", "image-with-text", { x: 0, y: 10, w: 12, h: 9 }, {
        heading: "Cold chain, all the way to the lift",
        body: "Milk, curd and paneer travel in an insulated box with a gel pack from the store to your door, and the rider is not allowed to leave them outside.",
        caption: "Sakinaka dark store",
        image: "/img/cat-dairy.svg",
        imageSide: "right",
        layout: "split",
        ctaLabel: "",
      }),
      sec("qc-prd-rail", "product-carousel", { x: 0, y: 19, w: 12, h: 7 }, {
        heading: "More from the dairy aisle",
        source: "category:dairy",
        limit: 8,
        ctaLabel: "See all",
        ctaHref: "/store/catalogue",
        cardWidth: 200,
        cardStyle: "compact",
        priceNote: "inclusive",
      }),
      sec("qc-prd-together", "product-carousel", { x: 0, y: 26, w: 12, h: 7 }, {
        heading: "Usually added with it",
        source: "category:staples",
        limit: 8,
        ctaLabel: "See all",
        ctaHref: "/store/catalogue",
        cardWidth: 200,
        cardStyle: "compact",
        priceNote: "inclusive",
      }),
      sec("qc-prd-faq", "faq", { x: 0, y: 33, w: 12, h: 6 }, {
        heading: "Delivery questions",
        q1: "What happens if something is out of stock when you pick it?",
        a1: "The rider calls before they leave the store. You choose a replacement or drop the line, and the refund is back on the same method within a day.",
        q2: "Is there a minimum order?",
        a2: "₹99 within the delivery radius. Below that a small-order fee of ₹25 applies, and it is shown before you pay.",
        q3: "How fresh is fresh?",
        a3: "Fruit and vegetables are sorted the same morning and nothing is carried over. Dairy is dated the day it reaches the store.",
        q4: "Can I return something?",
        a4: "Tell the rider at the door, or raise it within two hours. Anything perishable is refunded rather than collected.",
        openFirst: true,
      }),
      sec("qc-prd-footer", "footer-columns", { x: 0, y: 39, w: 12, h: 6 }, {
        ...FOOTER,
      }),
    ]),
  ],
};
