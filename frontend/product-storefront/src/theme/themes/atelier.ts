/**
 * Atelier — hand-made furniture, sold the way a showroom sells it.
 *
 * The second `retail` theme and the opposite end of the same library: a warm near-black ground,
 * antique brass, the largest spacing step and the widest content column in the set, and a serif
 * heading over a sans body — a pairing no other theme uses. Where QuickCart is a phone that
 * scrolls in thumb-widths, this is a desktop page built from full-bleed photographs with the words
 * on top of them, and a rail per collection with its link centred underneath.
 *
 * **No discount flags.** The catalogue carries a published MRP on most of these lines, so the card
 * *would* compute a saving and draw one. A showroom that shouts "22% OFF" over a hand-carved
 * rosewood table is selling something else, so every rail and grid here turns `showBadge` off.
 * `showEta` is left on deliberately: these lines carry no delivery promise — a workshop quotes
 * days, not minutes — so nothing paints, which is the guard doing its job rather than a setting
 * hiding the question.
 *
 * **Every source names a collection.** The retail fixture catalogue holds the grocery aisles too,
 * so `all`, `featured` and `in-stock` would put a packet of namkeen under "The Seating
 * Collection". Each block points at one of the four furniture categories.
 */

import type { Theme } from "../schema";
import { page, sec } from "./build";

/**
 * The ground an editorial band's scrim is mixed from — the theme's own `colorBg`, written out.
 *
 * `image-with-text` borrows the `contrast` ground for its overlay, which is the pair guaranteed to
 * be legible over a photograph in a *light* palette: dark words on a light wash. On a near-black
 * theme that inverts into a cream wash with dark words, which is the opposite of what the reference
 * shows. A tint takes precedence over the tone and pairs with `colorText`, so naming the dark
 * ground here is what puts cream words on a dark scrim. The hero has a `tone` control and uses
 * `surface` instead — no literal needed there.
 */
const SCRIM = "#17110C";

/**
 * The serviceability check, above the hero on the home page and above the piece on the detail
 * page — the two places somebody decides whether to keep reading.
 *
 * The showroom's answer is a **date**, not a duration: a workshop quotes days, and the zone's
 * `DATE_OFFSET` promise renders as "Arrives Wed 23 Sep". The date is resolved by the server in the
 * *store's* timezone and rendered exactly as sent (ADR-0010 §3) — a buyer in Singapore reading a
 * Jodhpur showroom's date shifted by a day is a support ticket nobody can diagnose.
 *
 * `idlePromiseText` is the claim the showroom makes before it knows where the buyer is; once it
 * knows, the zone's own answer replaces it.
 */
const CHECK = {
  storeSlug: "atelier",
  idlePromiseText: "Delivered and assembled anywhere in India",
  fieldLabel: "PIN code",
  submitLabel: "Check",
};

/**
 * The footer, which is identical on all three routes.
 *
 * Written once and spread into each page's instance: a footer that differs between routes is a
 * bug every time, and three copies of twenty settings is how that bug arrives. Each page still
 * gets its own `id`, because `SectionInstance.id` must be unique within the document.
 */
const FOOTER = {
  aboutTitle: "The workshop",
  aboutText:
    "Furniture made by hand in Jodhpur since [year founded], from timber we buy standing. Every piece is finished, photographed and shipped assembled.",
  col1Title: "Collections",
  col1Links:
    "Sofas & Seating | /store/catalogue?category=seating\nDining & Tables | /store/catalogue?category=dining\nBeds & Wardrobes | /store/catalogue?category=beds\nLighting & Décor | /store/catalogue?category=lighting",
  col2Title: "Buying from us",
  col2Links:
    "The full collection | /store/catalogue\nA piece in detail | /store/product\nMade to order | /store/product\nDelivery & assembly | /store/product",
  col3Title: "Atelier",
  col3Links: "Home | /store\nOur timber | /store/product\nThe showroom | /store/catalogue",
  col4Title: "Reach the showroom",
  col4Links:
    "[Phone number]\nstudio@example.in | mailto:studio@example.in\nMon to Sat, 10am to 7pm",
  socialTitle: "Follow",
  socialLinks: "Instagram | https://instagram.com\nPinterest | https://pinterest.com",
  paymentTitle: "We accept",
  paymentMethods: "UPI\nVisa\nMastercard\nRuPay\nNet banking\nBank transfer",
  legalText: "© [Year] [Legal entity name] · GSTIN [Your GSTIN] · All prices in INR, inclusive of GST",
  columns: 4,
};

export const atelier: Theme = {
  key: "atelier",
  name: "Atelier",
  description:
    "A hand-made furniture showroom in warm near-black and antique brass. Full-bleed photography with the words over it, a rail per collection, and a tall footer.",
  industry: "Furniture & home",
  mode: "retail",
  tokens: {
    colorBg: "#17110C",
    colorSurface: "#211A13",
    colorText: "#F4EDE3",
    colorMuted: "#B7A894",
    colorAccent: "#C9A227",
    colorAccentText: "#17110C",
    colorBorder: "#3A2E23",
    /* A serif that is actually installed. Didot on macOS, Georgia on Windows and on anything
       else — both are real files on the machine, because this app loads no webfonts and a face
       nobody has falls through in silence. */
    fontHeading: 'Didot, "Bodoni 72", "Hoefler Text", Georgia, "Times New Roman", serif',
    fontBody: '"Avenir Next", Avenir, "Segoe UI", system-ui, -apple-system, Helvetica, Arial, sans-serif',
    radius: 3,
    space: 20,
    maxWidth: 1440,
  },
  pages: [
    page("atl-home", "/", "Home", [
      sec("atl-delivery", "delivery-check", { x: 0, y: 0, w: 12, h: 3 }, { ...CHECK }),
      sec("atl-hero", "hero", { x: 0, y: 3, w: 12, h: 11 }, {
        eyebrow: "Made by hand in Jodhpur since [year founded]",
        heading: "Furniture that outlives the room it was made for",
        body: "Solid teak, sheesham and rosewood, cut and joined by hand in [your workshop's town].",
        ctaLabel: "Shop now",
        ctaHref: "/store/catalogue",
        secondaryLabel: "",
        /* The wide plate, not the 4:3 room tile. This hero is a 2:1 band, and `cover` on a 4:3
           source throws away the top and bottom third — the tile's sofa lost its feet and the
           lamp above it. `hero-atelier` is the same showroom composed for the band it lands in. */
        image: "/img/hero-atelier.svg",
        layout: "overlay",
        overlay: "gradient",
        imageFocus: "center",
        minHeight: 620,
        align: "left",
        tone: "surface",
        bleed: true,
      }),
      sec("atl-rooms", "collection-row", { x: 0, y: 14, w: 12, h: 5 }, {
        heading: "Shop by room",
        item1Title: "Sofas & Seating",
        item1Image: "/img/cat-seating.svg",
        item1Href: "/store/catalogue?category=seating",
        item2Title: "Dining & Tables",
        item2Image: "/img/cat-dining.svg",
        item2Href: "/store/catalogue?category=dining",
        item3Title: "Beds & Wardrobes",
        item3Image: "/img/cat-beds.svg",
        item3Href: "/store/catalogue?category=beds",
        item4Title: "Lighting & Décor",
        item4Image: "/img/cat-lighting.svg",
        item4Href: "/store/catalogue?category=lighting",
        columns: 4,
        mobileColumns: 2,
        aspect: "wide",
      }),
      sec("atl-rail-seating", "product-carousel", { x: 0, y: 19, w: 12, h: 9 }, {
        heading: "The Seating Collection",
        source: "category:seating",
        limit: 8,
        ctaLabel: "View all",
        ctaHref: "/store/catalogue?category=seating",
        ctaPlacement: "below",
        cardWidth: 320,
        cardStyle: "detail",
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-band-timber", "image-with-text", { x: 0, y: 28, w: 12, h: 6 }, {
        heading: "We buy the tree, not the plank",
        body: "Timber is bought standing, felled to our own schedule and air-dried for eighteen months before a saw touches it. It is the slowest part of the work and the only reason a joint still closes ten monsoons later.",
        caption: "The drying yard, Jodhpur",
        image: "/img/cat-beds.svg",
        layout: "overlay",
        overlay: "scrim",
        tint: SCRIM,
        ctaLabel: "See the collection",
        ctaHref: "/store/catalogue",
        bleed: true,
      }),
      sec("atl-rail-dining", "product-carousel", { x: 0, y: 34, w: 12, h: 9 }, {
        heading: "The Dining Collection",
        source: "category:dining",
        limit: 8,
        ctaLabel: "View all",
        ctaHref: "/store/catalogue?category=dining",
        ctaPlacement: "below",
        cardWidth: 320,
        cardStyle: "detail",
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-rail-beds", "product-carousel", { x: 0, y: 43, w: 12, h: 9 }, {
        heading: "Beds & Wardrobes",
        source: "category:beds",
        limit: 8,
        ctaLabel: "View all",
        ctaHref: "/store/catalogue?category=beds",
        ctaPlacement: "below",
        cardWidth: 320,
        cardStyle: "detail",
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-band-hands", "image-with-text", { x: 0, y: 52, w: 12, h: 5 }, {
        heading: "Eleven pairs of hands, one workshop",
        body: "Nothing here is assembled from a kit. A bed is cut, carved, fitted and finished by the same team, and the piece that arrives is the piece they photographed before it was wrapped.",
        caption: "Finishing bay, Jodhpur",
        image: "/img/cat-lighting.svg",
        layout: "overlay",
        overlay: "gradient",
        tint: SCRIM,
        ctaLabel: "",
        bleed: true,
      }),
      sec("atl-rail-lighting", "product-carousel", { x: 0, y: 57, w: 12, h: 9 }, {
        heading: "Lighting & Décor",
        source: "category:lighting",
        limit: 8,
        ctaLabel: "View all",
        ctaHref: "/store/catalogue?category=lighting",
        ctaPlacement: "below",
        cardWidth: 320,
        cardStyle: "detail",
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-closing", "rich-text", { x: 0, y: 66, w: 12, h: 5 }, {
        heading: "Made to order, delivered assembled",
        body: "Every piece is built after you order it, which is why a sofa takes three weeks and a wardrobe takes four.\n\nIt arrives assembled, carried in, placed where you want it and the packing taken away again. Nothing is left in a flat box on a landing.",
        size: "lg",
        align: "center",
        tone: "surface",
        columns: 1,
      }),
      sec("atl-footer", "footer-columns", { x: 0, y: 71, w: 12, h: 7 }, { ...FOOTER }),
    ]),

    page("atl-catalogue", "/catalogue", "The collection", [
      sec("atl-cat-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "The collection",
        heading: "Every piece we make",
        body: "Prices are inclusive of GST and include delivery and assembly anywhere in India.",
        ctaLabel: "",
        secondaryLabel: "",
        image: "/img/cat-dining.svg",
        layout: "overlay",
        overlay: "scrim",
        imageFocus: "center",
        minHeight: 360,
        align: "left",
        tone: "surface",
        bleed: true,
      }),
      sec("atl-cat-rooms", "collection-row", { x: 0, y: 6, w: 12, h: 5 }, {
        heading: "Shop by room",
        item1Title: "Sofas & Seating",
        item1Image: "/img/cat-seating.svg",
        item1Href: "/store/catalogue?category=seating",
        item2Title: "Dining & Tables",
        item2Image: "/img/cat-dining.svg",
        item2Href: "/store/catalogue?category=dining",
        item3Title: "Beds & Wardrobes",
        item3Image: "/img/cat-beds.svg",
        item3Href: "/store/catalogue?category=beds",
        item4Title: "Lighting & Décor",
        item4Image: "/img/cat-lighting.svg",
        item4Href: "/store/catalogue?category=lighting",
        columns: 4,
        mobileColumns: 2,
        aspect: "wide",
      }),
      sec("atl-cat-seating", "product-grid", { x: 0, y: 11, w: 12, h: 16 }, {
        heading: "Sofas & Seating",
        source: "category:seating",
        limit: 6,
        columns: 3,
        mobileColumns: 2,
        showCode: false,
        showStock: true,
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-cat-dining", "product-grid", { x: 0, y: 27, w: 12, h: 8 }, {
        heading: "Dining & Tables",
        source: "category:dining",
        limit: 6,
        columns: 3,
        mobileColumns: 2,
        showCode: false,
        showStock: true,
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-cat-beds", "product-grid", { x: 0, y: 35, w: 12, h: 16 }, {
        heading: "Beds & Wardrobes",
        source: "category:beds",
        limit: 6,
        columns: 3,
        mobileColumns: 2,
        showCode: false,
        showStock: true,
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-cat-lighting", "product-grid", { x: 0, y: 51, w: 12, h: 8 }, {
        heading: "Lighting & Décor",
        source: "category:lighting",
        limit: 6,
        columns: 3,
        mobileColumns: 2,
        showCode: false,
        showStock: true,
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-cat-footer", "footer-columns", { x: 0, y: 59, w: 12, h: 7 }, { ...FOOTER }),
    ]),

    page("atl-product", "/product", "A piece in detail", [
      sec("atl-prd-delivery", "delivery-check", { x: 0, y: 0, w: 12, h: 3 }, { ...CHECK }),
      sec("atl-prd-hero", "hero", { x: 0, y: 3, w: 12, h: 9 }, {
        eyebrow: "Sofas & Seating",
        heading: "Kashmir Walnut Three-Seater Sofa",
        body: "A hand-carved walnut frame under linen upholstery, 198 cm across. Built to order in about three weeks.",
        ctaLabel: "See the collection",
        ctaHref: "/store/catalogue?category=seating",
        secondaryLabel: "",
        image: "/img/p-sofa.svg",
        layout: "overlay",
        overlay: "gradient",
        imageFocus: "center",
        minHeight: 520,
        align: "left",
        tone: "surface",
        bleed: true,
      }),
      sec("atl-prd-band", "image-with-text", { x: 0, y: 12, w: 12, h: 8 }, {
        heading: "Carved, not pressed",
        body: "The chinar panel on the back of this frame is cut by hand from a single board. It takes one carver about nine days, and it is the reason no two of these sofas are quite identical.",
        caption: "Walnut carving, Srinagar",
        image: "/img/cat-seating.svg",
        imageSide: "left",
        layout: "split",
        ctaLabel: "",
      }),
      sec("atl-prd-specs", "feature-columns", { x: 0, y: 20, w: 12, h: 4 }, {
        heading: "What you are buying",
        columns: 3,
        item1Title: "Solid walnut",
        item1Body: "Kashmiri walnut, air-dried eighteen months, finished in beeswax rather than lacquer so it can be repaired at home.",
        item2Title: "Three weeks to build",
        item2Body: "Cutting starts the day the order is confirmed. You get a photograph of the frame before it is upholstered.",
        item3Title: "Delivered assembled",
        item3Body: "Carried in, placed, and the packing taken away again. Insured door to door anywhere in India.",
        item4Title: "",
        item4Body: "",
      }),
      sec("atl-prd-rail", "product-carousel", { x: 0, y: 24, w: 12, h: 9 }, {
        heading: "More from the Seating Collection",
        source: "category:seating",
        limit: 8,
        ctaLabel: "View all",
        ctaHref: "/store/catalogue?category=seating",
        ctaPlacement: "below",
        cardWidth: 320,
        cardStyle: "detail",
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-prd-pairs", "product-carousel", { x: 0, y: 33, w: 12, h: 9 }, {
        heading: "Pairs well with",
        source: "category:lighting",
        limit: 8,
        ctaLabel: "View all",
        ctaHref: "/store/catalogue?category=lighting",
        ctaPlacement: "below",
        cardWidth: 320,
        cardStyle: "detail",
        showBadge: false,
        priceNote: "inclusive",
      }),
      sec("atl-prd-faq", "faq", { x: 0, y: 42, w: 12, h: 6 }, {
        heading: "Before you order",
        q1: "How long does a made-to-order piece take?",
        a1: "Three weeks for seating, four for a bed or a wardrobe, and we confirm the week in writing before you pay anything.",
        q2: "Can I change the timber or the fabric?",
        a2: "Yes, on every piece. Send a fabric reference or ask for the swatch box, which goes out by courier at no charge.",
        q3: "What does delivery cost?",
        a3: "Nothing, anywhere in India. The piece is insured in transit, carried in, placed and unwrapped.",
        q4: "What if it arrives damaged?",
        a4: "Tell the crew at the door or us within 48 hours. We repair in your home where that is right, and replace where it is not.",
        openFirst: true,
      }),
      sec("atl-prd-footer", "footer-columns", { x: 0, y: 48, w: 12, h: 7 }, { ...FOOTER }),
    ]),
  ],
};
