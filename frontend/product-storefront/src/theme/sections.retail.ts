/**
 * The retail half of the section library — the four blocks the quick-commerce and the luxury
 * furniture references need and that the six original sections cannot express.
 *
 * They live in their own file for one practical reason: `sections.ts` is the file every other
 * change to the library touches, and four new definitions landing in the middle of it is a merge
 * conflict with whoever is editing the six that were already there. `sections.ts` merges this
 * array in on one line.
 *
 * Everything here obeys the same two contracts as the original six:
 *
 * - **Flat settings.** `SettingValue` is `string | number | boolean | null`, so a repeat is
 *   numbered keys (`item1Title`, `item2Title`, …) with empty defaults, and a list of links is a
 *   `textarea` with one entry per line. Renderers skip an empty slot rather than drawing a hole.
 * - **No ninth field type.** Every control below is one of the eight the editor already knows:
 *   tints are `color`, shape choices are `select`, link lists are `textarea`. A ninth would mean
 *   changing `schema.ts` and `SettingControl.tsx` together, and none of these four needs it.
 */

import { BLEED_SETTING } from "./schema";
import type { SectionDef, SettingField } from "./schema";

const ASPECT_OPTIONS = [
  { value: "wide", label: "Wide (3:2)" },
  { value: "square", label: "Square" },
  { value: "tall", label: "Tall (3:4)" },
];

/**
 * The full-width toggle, declared identically wherever it appears.
 *
 * The key is `BLEED_SETTING` rather than the string, because `PageRenderer` reads that same
 * constant off the instance — it is the one setting key the page layer understands. `defaultOn`
 * differs per section: a top strip and a footer reach the canvas edge in both references, while a
 * promo banner sits inside the content column unless the merchant says otherwise.
 */
function bleedField(defaultOn: boolean): SettingField {
  return {
    key: BLEED_SETTING,
    label: "Full width",
    type: "toggle",
    default: defaultOn,
    help: "Span the whole canvas instead of the content column.",
  };
}

export const RETAIL_SECTION_DEFS: SectionDef[] = [
  {
    /*
     * The strip above everything else: what the store promises on delivery, where it is
     * delivering, a search box, an account link and the basket.
     *
     * `layout`, not `content`, because it is chrome — a merchant reaches for it in the same breath
     * as a footer.
     */
    type: "announcement-bar",
    name: "Announcement bar",
    group: "layout",
    defaultLayout: { w: 12, h: 3 },
    fields: [
      {
        key: "promiseText",
        label: "Delivery promise",
        type: "text",
        default: "Delivery in 11 minutes",
        help: "Your own words. A store-wide promise, not a product's delivery estimate — those come off the catalogue line and are shown on the card.",
      },
      { key: "locationLabel", label: "Address label", type: "text", default: "Deliver to" },
      {
        key: "locationText",
        label: "Address",
        type: "text",
        default: "Sakinaka, Andheri East, Mumbai 400072",
      },
      { key: "showSearch", label: "Show the search box", type: "toggle", default: true },
      {
        key: "searchLabel",
        label: "Search label",
        type: "text",
        default: "Search the catalogue",
        help: "The control's name, read out by a screen reader. A description, not a placeholder.",
      },
      {
        key: "searchPlaceholder",
        label: "Search placeholder",
        type: "text",
        default: 'Search for "atta"',
      },
      {
        key: "searchHref",
        label: "Search results page",
        type: "text",
        default: "/store/catalogue",
        help: "Where a search goes. The typed words are added as ?q=.",
      },
      { key: "accountLabel", label: "Account link", type: "text", default: "Account" },
      { key: "accountHref", label: "Account link target", type: "text", default: "/store/contact" },
      {
        key: "showCart",
        label: "Show the basket",
        type: "toggle",
        default: true,
        help: "Shows what is in the basket. Not a link — there is no basket page to send anyone to yet.",
      },
      {
        key: "cartLabel",
        label: "Basket label",
        type: "text",
        default: "",
        help: "Leave empty to use the wording that matches the store's mode — a basket for retail, a line list for trade.",
      },
      {
        key: "tint",
        label: "Background tint",
        type: "color",
        default: null,
        help: "Leave empty for the theme's surface colour.",
      },
      bleedField(true),
    ],
  },
  {
    /*
     * "Shop by store" — wide tiles to destinations the merchant chooses.
     *
     * Deliberately not `category-tiles`. That section is bound to the `categories` prop and shows
     * what the catalogue actually contains; these four are arbitrary — a seasonal landing page, a
     * brand, an offer, an external microsite — and a merchant authors each one by hand.
     */
    type: "collection-row",
    name: "Collection row",
    group: "commerce",
    defaultLayout: { w: 12, h: 6 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Shop by store" },
      { key: "item1Title", label: "Tile 1 title", type: "text", default: "Grocery & Kitchen" },
      { key: "item1Image", label: "Tile 1 image", type: "image", default: "/img/cat-staples.svg" },
      { key: "item1Href", label: "Tile 1 link", type: "text", default: "/store/catalogue" },
      { key: "item1Tint", label: "Tile 1 tint", type: "color", default: null },
      { key: "item2Title", label: "Tile 2 title", type: "text", default: "Snacks & Drinks" },
      { key: "item2Image", label: "Tile 2 image", type: "image", default: "/img/cat-snacks.svg" },
      { key: "item2Href", label: "Tile 2 link", type: "text", default: "/store/catalogue" },
      { key: "item2Tint", label: "Tile 2 tint", type: "color", default: null },
      { key: "item3Title", label: "Tile 3 title", type: "text", default: "Home & Cleaning" },
      { key: "item3Image", label: "Tile 3 image", type: "image", default: "/img/cat-household.svg" },
      { key: "item3Href", label: "Tile 3 link", type: "text", default: "/store/catalogue" },
      { key: "item3Tint", label: "Tile 3 tint", type: "color", default: null },
      { key: "item4Title", label: "Tile 4 title", type: "text", default: "" },
      { key: "item4Image", label: "Tile 4 image", type: "image", default: null },
      { key: "item4Href", label: "Tile 4 link", type: "text", default: "" },
      { key: "item4Tint", label: "Tile 4 tint", type: "color", default: null },
      {
        key: "columns",
        label: "Columns",
        type: "number",
        default: 3,
        help: "2 to 4, on a wide screen.",
      },
      {
        key: "mobileColumns",
        label: "Columns on a phone",
        type: "number",
        default: 2,
        help: "1 to 3. Two is the reference layout.",
      },
      { key: "aspect", label: "Tile shape", type: "select", options: ASPECT_OPTIONS, default: "wide" },
      {
        key: "scroll",
        label: "Scroll sideways on a phone",
        type: "toggle",
        default: false,
        help: "A swipeable row instead of a grid. The next tile peeks in, so it is clear there is more.",
      },
    ],
  },
  {
    /*
     * The pastel banner: a headline, a sub-line, an illustration and up to four small tiles inside
     * it.
     *
     * Its heading is an `h2`. A page has exactly one `h1` and the hero owns it — a banner further
     * down competing for that rank is a broken outline, and a test pins it.
     */
    type: "promo-banner",
    name: "Promo banner",
    group: "content",
    defaultLayout: { w: 12, h: 8 },
    fields: [
      { key: "eyebrow", label: "Eyebrow", type: "text", default: "This week" },
      { key: "heading", label: "Heading", type: "text", default: "Monsoon essentials, delivered" },
      {
        key: "body",
        label: "Supporting copy",
        type: "textarea",
        default: "Umbrellas, hot drinks and everything else that keeps the week going.",
      },
      { key: "image", label: "Illustration", type: "image", default: null },
      {
        key: "tint",
        label: "Background tint",
        type: "color",
        default: null,
        help: "A pale ground. Leave empty for the theme's surface colour.",
      },
      { key: "ctaLabel", label: "Button", type: "text", default: "Shop the edit" },
      { key: "ctaHref", label: "Button link", type: "text", default: "/store/catalogue" },
      { key: "tile1Title", label: "Tile 1 title", type: "text", default: "Hot drinks" },
      { key: "tile1Image", label: "Tile 1 image", type: "image", default: "/img/p-tea.svg" },
      { key: "tile1Href", label: "Tile 1 link", type: "text", default: "/store/catalogue" },
      { key: "tile2Title", label: "Tile 2 title", type: "text", default: "Biscuits" },
      { key: "tile2Image", label: "Tile 2 image", type: "image", default: "/img/p-biscuits.svg" },
      { key: "tile2Href", label: "Tile 2 link", type: "text", default: "/store/catalogue" },
      { key: "tile3Title", label: "Tile 3 title", type: "text", default: "Namkeen" },
      { key: "tile3Image", label: "Tile 3 image", type: "image", default: "/img/p-namkeen.svg" },
      { key: "tile3Href", label: "Tile 3 link", type: "text", default: "/store/catalogue" },
      { key: "tile4Title", label: "Tile 4 title", type: "text", default: "" },
      { key: "tile4Image", label: "Tile 4 image", type: "image", default: null },
      { key: "tile4Href", label: "Tile 4 link", type: "text", default: "" },
      bleedField(false),
    ],
  },
  {
    /*
     * The tall multi-column footer.
     *
     * A real `<footer>`, which is the whole point: the reference's footer *is* the page's footer,
     * so making it a `<div>` to dodge the chrome footer would put the landmark on the wrong
     * element. The store chrome stands its own footer down when a page carries this section.
     *
     * Payment methods are **words in the body face**, not brand marks. We have no licence to the
     * card schemes' artwork, and a store shipping someone else's logo without one is a problem for
     * the merchant, not for us.
     */
    type: "footer-columns",
    name: "Footer columns",
    group: "layout",
    defaultLayout: { w: 12, h: 11 },
    fields: [
      { key: "aboutTitle", label: "About heading", type: "text", default: "About us" },
      {
        key: "aboutText",
        label: "About copy",
        type: "textarea",
        default: "Furniture made by hand in Jodhpur since 1994, shipped assembled and insured across India.",
      },
      { key: "col1Title", label: "Column 1 heading", type: "text", default: "Shop" },
      {
        key: "col1Links",
        label: "Column 1 links",
        type: "textarea",
        default:
          "Seating | /store/catalogue\nBeds | /store/catalogue\nDining | /store/catalogue\nLighting | /store/catalogue",
        help: "One per line, as Label | /link. A line with no | is shown as plain text.",
      },
      { key: "col2Title", label: "Column 2 heading", type: "text", default: "Help" },
      {
        key: "col2Links",
        label: "Column 2 links",
        type: "textarea",
        default:
          "Delivery & assembly | /store/contact\nReturns | /store/contact\nCare guide | /store/about\nTrack an order | /store/contact",
        help: "One per line, as Label | /link. A line with no | is shown as plain text.",
      },
      { key: "col3Title", label: "Column 3 heading", type: "text", default: "Company" },
      {
        key: "col3Links",
        label: "Column 3 links",
        type: "textarea",
        default:
          "Our workshop | /store/about\nStockists | /store/contact\nPress | /store/about\nCareers | /store/contact",
        help: "One per line, as Label | /link. A line with no | is shown as plain text.",
      },
      { key: "col4Title", label: "Column 4 heading", type: "text", default: "Contact" },
      {
        key: "col4Links",
        label: "Column 4 links",
        type: "textarea",
        default:
          "+91 291 244 0000 | tel:+912912440000\nhello@example.in | mailto:hello@example.in\nMon to Sat, 10am to 7pm",
        help: "One per line, as Label | /link. A line with no | is shown as plain text — the opening hours, not a link.",
      },
      { key: "socialTitle", label: "Social heading", type: "text", default: "Follow" },
      {
        key: "socialLinks",
        label: "Social links",
        type: "textarea",
        default: "Instagram | https://instagram.com\nPinterest | https://pinterest.com",
        help: "One per line, as Label | https://link.",
      },
      { key: "paymentTitle", label: "Payment heading", type: "text", default: "We accept" },
      {
        key: "paymentMethods",
        label: "Payment methods",
        type: "textarea",
        default: "UPI\nVisa\nMastercard\nRuPay\nNet banking",
        help: "One per line. Shown as words, not logos — the card schemes' marks are licensed artwork we do not have.",
      },
      {
        key: "legalText",
        label: "Legal line",
        type: "text",
        default: "© 2026 · GSTIN 08AAACR0000R1ZX · All prices in INR",
      },
      { key: "columns", label: "Link columns", type: "number", default: 4, help: "2 to 4." },
      {
        key: "tint",
        label: "Background tint",
        type: "color",
        default: null,
        help: "Leave empty for the theme's surface colour.",
      },
      bleedField(true),
    ],
  },
];
