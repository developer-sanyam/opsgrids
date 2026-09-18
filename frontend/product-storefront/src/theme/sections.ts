/**
 * The section library — every block a merchant can place on a page, and every setting that block
 * exposes.
 *
 * This is the single source of truth for both halves of the product: the editor builds its palette
 * and its settings panel from these definitions, and the renderer reads the same `settings` keys
 * back out. A field that is not declared here has no control in the editor, so a renderer must
 * never read one.
 *
 * Settings are flat by contract (`Record<string, SettingValue>`), so a repeating block — three
 * feature columns, four FAQ entries — is expressed as numbered keys rather than an array. It is
 * verbose, and it is what keeps one control per field in the editor with no nested-array UI.
 */

import { BLEED_SETTING } from "./schema";
import type { SectionDef, SettingField, SettingValue } from "./schema";
import { CMS_SECTION_DEFS } from "./sections.cms";
import { RETAIL_SECTION_DEFS } from "./sections.retail";
import { PRODUCT_SOURCE_OPTIONS } from "./sources";

const ALIGN_OPTIONS = [
  { value: "left", label: "Left" },
  { value: "center", label: "Centred" },
];

const TONE_OPTIONS = [
  { value: "surface", label: "Surface" },
  { value: "accent", label: "Accent" },
  { value: "contrast", label: "Contrast" },
];

const PRICE_NOTE_OPTIONS = [
  { value: "exclusive", label: "Prices exclusive of GST" },
  { value: "inclusive", label: "Prices inclusive of GST" },
  { value: "on-request", label: "Price on request" },
];

/**
 * How a band section arranges its picture and its words.
 *
 * `split` is the two-column arrangement every trade theme already ships, and it stays the default
 * so adopting this field changes nothing. `overlay` is the photograph with the words on top of it.
 */
const BAND_LAYOUT_OPTIONS = [
  { value: "split", label: "Side by side" },
  { value: "overlay", label: "Words over the picture" },
];

/** What sits between an overlay band's photograph and its words. Ignored by `split`. */
const OVERLAY_OPTIONS = [
  { value: "none", label: "Nothing" },
  { value: "scrim", label: "Even scrim" },
  { value: "gradient", label: "Gradient from the foot" },
];

/** Which part of a cropped photograph survives the crop. */
const IMAGE_FOCUS_OPTIONS = [
  { value: "center", label: "Centre" },
  { value: "top", label: "Top" },
  { value: "bottom", label: "Bottom" },
  { value: "left", label: "Left" },
  { value: "right", label: "Right" },
];

/** `TONE_OPTIONS` plus the "paint no ground at all" case, which is what prose usually wants. */
const PANEL_TONE_OPTIONS = [{ value: "none", label: "No ground" }, ...TONE_OPTIONS];

/**
 * How large a block of prose is set. These are the heading scale's own five steps, by name, and
 * they must stay in step with `SIZES` in `RichText.tsx` — an option the renderer does not accept
 * is a control that silently does nothing.
 *
 * "Display" moved up one: it now names the top step rather than `xl`, which is the step that makes
 * a closing wordmark read as a wordmark. Only the label moved — `xl` keeps its stored value, so no
 * store that already chose it changes.
 */
const TEXT_SIZE_OPTIONS = [
  { value: "sm", label: "Small" },
  { value: "md", label: "Medium" },
  { value: "lg", label: "Large" },
  { value: "xl", label: "Extra large" },
  { value: "display", label: "Display" },
];

const CARD_STYLE_OPTIONS = [
  { value: "detail", label: "With the spec line" },
  { value: "compact", label: "Compact" },
];

/**
 * The shape of a category tile's picture.
 *
 * Nothing wider than 16:9 is offered, on purpose. The bundled category art is drawn 4:3 with the
 * subject centred and the tile covers its box, so a wider frame crops the top and the bottom off
 * the silhouette — which is the only thing the drawing has.
 */
const TILE_ASPECT_OPTIONS = [
  { value: "square", label: "Square" },
  { value: "landscape", label: "Landscape (4:3)" },
  { value: "wide", label: "Wide (16:9)" },
];

const CTA_PLACEMENT_OPTIONS = [
  { value: "trailing", label: "Beside the heading" },
  { value: "below", label: "Centred below the row" },
];

/**
 * The one setting key the page layer reads back off an instance.
 *
 * Declared through a helper so every section that offers it spells `BLEED_SETTING` and never a
 * retyped string — the whole reason that constant exists.
 */
function bleedField(): SettingField {
  return {
    key: BLEED_SETTING,
    label: "Full width",
    type: "toggle",
    default: false,
    help: "Span the page edge to edge instead of sitting in the content column.",
  };
}

/**
 * The card options every section that renders a `ProductCard` offers.
 *
 * One list, so the same control is spelled the same way in the grid and in the rail and a merchant
 * cannot find two settings that look identical and behave differently. `showBadge` and `showEta`
 * are permissions, not instructions: the card still requires a retail store *and* non-null data
 * before either one paints.
 */
function cardOptionFields(): SettingField[] {
  return [
    { key: "cardStyle", label: "Card style", type: "select", options: CARD_STYLE_OPTIONS, default: "detail" },
    {
      key: "showBadge",
      label: "Show the discount flag",
      type: "toggle",
      default: true,
      help: "Retail stores only, and only where a published MRP is above the selling price.",
    },
    {
      key: "showEta",
      label: "Show the delivery time",
      type: "toggle",
      default: true,
      help: "Retail stores only, and only where the line carries a delivery promise.",
    },
    { key: "showVariants", label: "Show the pack-size selector", type: "toggle", default: true },
  ];
}

/** A `product-source` control, used by every commerce section. */
function sourceField(help: string): SettingField {
  return {
    key: "source",
    label: "Products from",
    type: "product-source",
    options: PRODUCT_SOURCE_OPTIONS,
    default: "all",
    help,
  };
}

const CORE_SECTION_DEFS: SectionDef[] = [
  {
    type: "hero",
    name: "Hero",
    group: "content",
    defaultLayout: { w: 12, h: 9 },
    fields: [
      { key: "eyebrow", label: "Eyebrow", type: "text", default: "Since 1994" },
      { key: "heading", label: "Heading", type: "text", default: "Industrial supply, without the follow-up calls" },
      {
        key: "body",
        label: "Supporting copy",
        type: "textarea",
        default: "Live stock, dealer pricing and dispatch the same working day from three regional warehouses.",
      },
      { key: "ctaLabel", label: "Primary button", type: "text", default: "Browse the catalogue" },
      { key: "ctaHref", label: "Primary button link", type: "text", default: "/store/catalogue" },
      { key: "secondaryLabel", label: "Secondary button", type: "text", default: "Request a quote" },
      { key: "secondaryHref", label: "Secondary button link", type: "text", default: "/store/catalogue" },
      { key: "image", label: "Image", type: "image", default: null, help: "Leave empty to use the theme's generated plate." },
      { key: "align", label: "Alignment", type: "select", options: ALIGN_OPTIONS, default: "left" },
      { key: "tone", label: "Tone", type: "select", options: TONE_OPTIONS, default: "contrast" },
      { key: "layout", label: "Layout", type: "select", options: BAND_LAYOUT_OPTIONS, default: "split" },
      {
        key: "overlay",
        label: "Overlay",
        type: "select",
        options: OVERLAY_OPTIONS,
        default: "scrim",
        help: "Words-over-the-picture only. With nothing between them, the heading is only as readable as the photograph under it.",
      },
      {
        key: "imageFocus",
        label: "Focal point",
        type: "select",
        options: IMAGE_FOCUS_OPTIONS,
        default: "center",
        help: "Which part of the picture survives the crop. Words-over-the-picture only.",
      },
      {
        key: "minHeight",
        label: "Minimum height",
        type: "number",
        default: 420,
        help: "In pixels, 240 to 720. Words-over-the-picture only.",
      },
      bleedField(),
    ],
  },
  {
    type: "rich-text",
    name: "Rich text",
    group: "content",
    defaultLayout: { w: 8, h: 5 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Who we supply" },
      {
        key: "body",
        label: "Body",
        type: "textarea",
        default:
          "We stock for contractors, OEM purchase teams and regional dealers. Orders above the MOQ ship on a 24-hour dispatch commitment; everything else moves on the next scheduled run.",
      },
      { key: "align", label: "Alignment", type: "select", options: ALIGN_OPTIONS, default: "left" },
      { key: "columns", label: "Text columns", type: "number", default: 1, help: "1 or 2." },
      {
        key: "size",
        label: "Size",
        type: "select",
        options: TEXT_SIZE_OPTIONS,
        default: "lg",
        help: "Display sets the heading at the top of the theme's scale — a closing wordmark, not a paragraph.",
      },
      { key: "tone", label: "Ground", type: "select", options: PANEL_TONE_OPTIONS, default: "none" },
      bleedField(),
    ],
  },
  {
    type: "image-with-text",
    name: "Image with text",
    group: "content",
    defaultLayout: { w: 12, h: 7 },
    fields: [
      { key: "image", label: "Image", type: "image", default: null },
      {
        key: "imageSide",
        label: "Image side",
        type: "select",
        options: [
          { value: "left", label: "Left" },
          { value: "right", label: "Right" },
        ],
        default: "left",
      },
      { key: "heading", label: "Heading", type: "text", default: "Twelve thousand square feet, one shift" },
      {
        key: "body",
        label: "Body",
        type: "textarea",
        default:
          "Bulk breaking, re-packing and labelling happen in-house, so a mixed pallet leaves as one consignment with one invoice.",
      },
      { key: "caption", label: "Caption", type: "text", default: "Bhiwandi distribution centre" },
      { key: "ctaLabel", label: "Button", type: "text", default: "" },
      { key: "ctaHref", label: "Button link", type: "text", default: "/store/catalogue" },
      { key: "layout", label: "Layout", type: "select", options: BAND_LAYOUT_OPTIONS, default: "split" },
      {
        key: "overlay",
        label: "Overlay",
        type: "select",
        options: OVERLAY_OPTIONS,
        default: "scrim",
        help: "Words-over-the-picture only.",
      },
      {
        key: "tint",
        label: "Ground tint",
        type: "color",
        default: null,
        help: "Leave empty for no ground. A brand surface colour, not a link colour — the words on it stay the theme's text.",
      },
      bleedField(),
    ],
  },
  {
    type: "feature-columns",
    name: "Feature columns",
    group: "content",
    defaultLayout: { w: 12, h: 6 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Why buyers stay" },
      { key: "columns", label: "Columns", type: "number", default: 3, help: "2, 3 or 4." },
      { key: "item1Title", label: "Column 1 title", type: "text", default: "Same-day dispatch" },
      { key: "item1Body", label: "Column 1 body", type: "textarea", default: "Cut-off is 3pm IST for stocked lines across all three warehouses." },
      { key: "item2Title", label: "Column 2 title", type: "text", default: "Test certificates" },
      { key: "item2Body", label: "Column 2 body", type: "textarea", default: "Mill and batch certificates travel with the consignment, not by email a week later." },
      { key: "item3Title", label: "Column 3 title", type: "text", default: "Credit terms" },
      { key: "item3Body", label: "Column 3 body", type: "textarea", default: "Net 30 on approved accounts, with a standing PO option for repeat lines." },
      { key: "item4Title", label: "Column 4 title", type: "text", default: "" },
      { key: "item4Body", label: "Column 4 body", type: "textarea", default: "" },
    ],
  },
  {
    type: "product-grid",
    name: "Product grid",
    group: "commerce",
    defaultLayout: { w: 12, h: 12 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Stocked lines" },
      sourceField("Which slice of the catalogue this grid draws from."),
      { key: "limit", label: "Maximum items", type: "number", default: 8 },
      { key: "columns", label: "Columns", type: "number", default: 4, help: "2, 3 or 4." },
      {
        key: "mobileColumns",
        label: "Columns on a phone",
        type: "number",
        default: 2,
        help: "1 to 4. Applies below the stacking width.",
      },
      { key: "showCode", label: "Show product code", type: "toggle", default: true },
      { key: "showPrice", label: "Show price", type: "toggle", default: true },
      { key: "showStock", label: "Show stock and lead time", type: "toggle", default: true },
      { key: "priceNote", label: "Price basis", type: "select", options: PRICE_NOTE_OPTIONS, default: "exclusive" },
      ...cardOptionFields(),
    ],
  },
  {
    type: "product-carousel",
    name: "Product carousel",
    group: "commerce",
    defaultLayout: { w: 12, h: 8 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Moving this week" },
      sourceField("Which slice of the catalogue this row draws from."),
      { key: "limit", label: "Maximum items", type: "number", default: 10 },
      { key: "showPrice", label: "Show price", type: "toggle", default: true },
      { key: "ctaLabel", label: "Trailing link", type: "text", default: "See all" },
      { key: "ctaHref", label: "Trailing link target", type: "text", default: "/store/catalogue" },
      {
        key: "ctaPlacement",
        label: "Link placement",
        type: "select",
        options: CTA_PLACEMENT_OPTIONS,
        default: "trailing",
      },
      {
        key: "cardWidth",
        label: "Card width",
        type: "number",
        default: 230,
        help: "In pixels, 140 to 420. On a phone a card never takes more than about half the canvas, so part of the next one always shows and the row reads as scrollable.",
      },
      { key: "priceNote", label: "Price basis", type: "select", options: PRICE_NOTE_OPTIONS, default: "exclusive" },
      ...cardOptionFields(),
    ],
  },
  {
    type: "price-table",
    name: "Price table",
    group: "commerce",
    defaultLayout: { w: 12, h: 10 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Dealer price list" },
      sourceField("Which lines the table prices."),
      { key: "limit", label: "Maximum rows", type: "number", default: 8 },
      { key: "showMoq", label: "Show MOQ", type: "toggle", default: true },
      { key: "showUnit", label: "Show unit", type: "toggle", default: true },
      { key: "priceNote", label: "Price basis", type: "select", options: PRICE_NOTE_OPTIONS, default: "exclusive" },
      { key: "note", label: "Footnote", type: "text", default: "Slab pricing applies above 10× MOQ. Freight extra." },
    ],
  },
  {
    type: "category-tiles",
    name: "Category tiles",
    group: "commerce",
    defaultLayout: { w: 12, h: 7 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Shop by category" },
      { key: "limit", label: "Maximum tiles", type: "number", default: 6 },
      { key: "columns", label: "Columns", type: "number", default: 3, help: "2 to 6." },
      {
        key: "mobileColumns",
        label: "Columns on a phone",
        type: "number",
        default: 4,
        help: "2 to 4. Applies below the stacking width — a phone shows a category grid, not a column of cards.",
      },
      { key: "showCount", label: "Show line count", type: "toggle", default: true },
      {
        key: "showBlurb",
        label: "Show the category description",
        type: "toggle",
        default: true,
        help: "Dropped automatically once the tiles are too narrow to carry a sentence.",
      },
      {
        key: "style",
        label: "Style",
        type: "select",
        options: [
          { value: "tile", label: "Tiles" },
          { value: "list", label: "List" },
          { value: "photo", label: "Photographs" },
          { value: "compact", label: "Compact panels" },
        ],
        default: "tile",
      },
      {
        key: "aspect",
        label: "Picture shape",
        type: "select",
        options: TILE_ASPECT_OPTIONS,
        default: "landscape",
        help: "Photograph and compact-panel styles only.",
      },
      {
        key: "tint",
        label: "Tile tint",
        type: "color",
        default: null,
        help: "Leave empty for the theme's card colour. A brand surface colour — the label on it stays the theme's text.",
      },
    ],
  },
  {
    type: "logo-wall",
    name: "Logo wall",
    group: "social",
    defaultLayout: { w: 12, h: 4 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Supplying" },
      {
        key: "names",
        label: "Company names",
        type: "textarea",
        default: "Godrej & Boyce\nThermax\nKirloskar Brothers\nTata Projects\nShapoorji Pallonji\nAshok Leyland",
        help: "One per line. Rendered as wordmarks in the theme's heading face.",
      },
      { key: "muted", label: "Mute the wordmarks", type: "toggle", default: true },
    ],
  },
  {
    type: "testimonials",
    name: "Testimonials",
    group: "social",
    defaultLayout: { w: 12, h: 7 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "What purchase teams say" },
      { key: "quote1", label: "Quote 1", type: "textarea", default: "Two years of monthly orders and not one short shipment. That is the whole review." },
      { key: "author1", label: "Author 1", type: "text", default: "Rajeev Bhatia" },
      { key: "role1", label: "Role 1", type: "text", default: "Purchase Head, Sunrise Engineering" },
      { key: "quote2", label: "Quote 2", type: "textarea", default: "They quote against our drawing, not against a category. It saves a day every time." },
      { key: "author2", label: "Author 2", type: "text", default: "Meenakshi Rao" },
      { key: "role2", label: "Role 2", type: "text", default: "Projects Buyer, Deccan Infra" },
      { key: "quote3", label: "Quote 3", type: "textarea", default: "" },
      { key: "author3", label: "Author 3", type: "text", default: "" },
      { key: "role3", label: "Role 3", type: "text", default: "" },
    ],
  },
  {
    type: "faq",
    name: "FAQ",
    group: "content",
    defaultLayout: { w: 8, h: 9 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Ordering questions" },
      { key: "q1", label: "Question 1", type: "text", default: "What is the minimum order value?" },
      { key: "a1", label: "Answer 1", type: "textarea", default: "₹5,000 excluding GST for delivery within the metro, ₹15,000 upcountry." },
      { key: "q2", label: "Question 2", type: "text", default: "Do you supply against a rate contract?" },
      { key: "a2", label: "Answer 2", type: "textarea", default: "Yes. Send the schedule and we will hold the rate for the contract period, subject to a quarterly review." },
      { key: "q3", label: "Question 3", type: "text", default: "How are freight and packing charged?" },
      { key: "a3", label: "Answer 3", type: "textarea", default: "At actuals, shown as separate lines on the invoice. Packing is free on full-case quantities." },
      { key: "q4", label: "Question 4", type: "text", default: "" },
      { key: "a4", label: "Answer 4", type: "textarea", default: "" },
      { key: "openFirst", label: "Open the first answer", type: "toggle", default: true },
    ],
  },
  {
    type: "enquiry-form",
    name: "Enquiry form",
    group: "commerce",
    defaultLayout: { w: 6, h: 12 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Ask for a quote" },
      {
        key: "body",
        label: "Intro copy",
        type: "textarea",
        default: "Tell us the line and the quantity. A regional manager replies the same working day.",
      },
      { key: "showQuantity", label: "Ask for quantity", type: "toggle", default: true },
      { key: "showGstin", label: "Ask for GSTIN", type: "toggle", default: true },
      { key: "submitLabel", label: "Submit button", type: "text", default: "Send enquiry" },
      { key: "successMessage", label: "Success message", type: "text", default: "Enquiry received. We will reply to the email you gave us." },
      { key: "consentText", label: "Consent line", type: "text", default: "We use these details to answer this enquiry and nothing else." },
    ],
  },
  {
    type: "contact-block",
    name: "Contact block",
    group: "content",
    defaultLayout: { w: 6, h: 8 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Reach the desk" },
      { key: "address", label: "Address", type: "textarea", default: "Plot 14, MIDC Phase II\nBhiwandi, Maharashtra 421302" },
      { key: "phone", label: "Phone", type: "text", default: "+91 22 4000 1180" },
      { key: "email", label: "Email", type: "text", default: "sales@example.co.in" },
      { key: "gstin", label: "GSTIN", type: "text", default: "27AABCU9603R1ZX" },
      { key: "hours", label: "Hours", type: "text", default: "Mon–Sat, 9:30am–6:30pm IST" },
    ],
  },
  {
    type: "footer-cta",
    name: "Footer call to action",
    group: "layout",
    defaultLayout: { w: 12, h: 6 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Open a trade account" },
      {
        key: "body",
        label: "Supporting copy",
        type: "textarea",
        default: "Credit terms, dealer pricing and a named account manager. Approval takes two working days.",
      },
      { key: "ctaLabel", label: "Button", type: "text", default: "Apply for an account" },
      { key: "ctaHref", label: "Button link", type: "text", default: "/store/catalogue" },
      { key: "tone", label: "Tone", type: "select", options: TONE_OPTIONS, default: "accent" },
      { key: "showBrand", label: "Show the brand line", type: "toggle", default: true },
    ],
  },
];

/** The whole library: the original blocks, the retail four, then the content seven. */
export const SECTION_DEFS: SectionDef[] = [
  ...CORE_SECTION_DEFS,
  ...RETAIL_SECTION_DEFS,
  ...CMS_SECTION_DEFS,
];

/** Indexed by `type`, for the renderer and the editor's lookups. */
export const SECTION_DEF_BY_TYPE: Record<string, SectionDef> = Object.fromEntries(
  SECTION_DEFS.map((def) => [def.type, def]),
);

export function sectionDef(type: string): SectionDef | null {
  return SECTION_DEF_BY_TYPE[type] ?? null;
}

/** Every declared default, for a freshly dropped instance. */
export function defaultSettings(type: string): Record<string, SettingValue> {
  const def = sectionDef(type);
  if (!def) return {};
  return Object.fromEntries(def.fields.map((f) => [f.key, f.default]));
}

/** The palette's groups, in the order it shows them. */
export const SECTION_GROUPS: { key: SectionDef["group"]; label: string }[] = [
  { key: "content", label: "Content" },
  { key: "commerce", label: "Commerce" },
  { key: "social", label: "Proof" },
  { key: "layout", label: "Layout" },
];
