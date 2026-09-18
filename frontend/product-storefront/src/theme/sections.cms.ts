/**
 * The content half of the section library — the seven blocks that make a merchant's *company*,
 * *support* and *legal* pages composable.
 *
 * The brief behind them is a ~120-page sitemap. About thirty-five of those pages — About, Our
 * Story, Why Choose Us, Team, Brands, Certifications, Quality & Manufacturing, Infrastructure,
 * Clients, Case Studies, Projects, Testimonials, Gallery, Videos, Careers, Contact, Help Centre,
 * FAQs, Shipping, Delivery, Returns, Cancellation, Replacement, Warranty, Payment Information and
 * nine legal pages — are not thirty-five designs. They are a handful of shapes carrying different
 * copy, and the only reason they could not be built already is that the library had no interior
 * page title, no structured prose, no picture grid and no people grid.
 *
 * They live in their own file for the same reason the retail four do: `sections.ts` is the file
 * every other change to the library touches, and a third block of definitions landing in the
 * middle of it is a merge conflict with whoever is editing the ones already there. `sections.ts`
 * merges this array in on one line.
 *
 * The three contracts every definition here keeps:
 *
 * - **Flat settings.** `SettingValue` is `string | number | boolean | null`, so a repeat is
 *   numbered keys with empty-string defaults and renderers skip an empty slot rather than drawing
 *   a hole. The one place that would have been unbearable — fifteen pages of policy prose — is
 *   solved by *parsing* a single authored field instead of adding slots (see `render/prose.ts`).
 * - **No ninth field type.** Every control below is one of the eight the editor already knows.
 * - **Every field has a default**, so a freshly dropped instance is complete and renders.
 */

import { BLEED_SETTING } from "./schema";
import type { SectionDef, SettingField } from "./schema";

const ALIGN_OPTIONS = [
  { value: "left", label: "Left" },
  { value: "center", label: "Centred" },
];

/** `none` paints no ground at all, which is what most interior pages want. Matches `BAND_TONES`. */
const GROUND_OPTIONS = [
  { value: "none", label: "No ground" },
  { value: "surface", label: "Surface" },
  { value: "accent", label: "Accent" },
  { value: "contrast", label: "Contrast" },
];

/**
 * The shape a picture is cropped to. `none` is a real answer for a card deck — a Careers page is a
 * list of roles and has no photography at all.
 */
const PICTURE_SHAPE_OPTIONS = [
  { value: "wide", label: "Wide (3:2)" },
  { value: "landscape", label: "Landscape (4:3)" },
  { value: "square", label: "Square" },
  { value: "tall", label: "Tall (3:4)" },
];

/** Portraits, plus the round crop a leadership row usually wants. */
const PORTRAIT_SHAPE_OPTIONS = [
  { value: "square", label: "Square" },
  { value: "tall", label: "Portrait (3:4)" },
  { value: "circle", label: "Circle" },
];

function bleedField(help: string): SettingField {
  return {
    key: BLEED_SETTING,
    label: "Full width",
    type: "toggle",
    default: false,
    help,
  };
}

/* ------------------------------ repeat slots ------------------------------ */

/**
 * The numbered-slot helpers.
 *
 * Written as functions rather than a hundred near-identical literal lines, because the thing that
 * matters about a slot is that every one of them is spelled *identically* — `item3Caption` has to
 * be `item3Caption` in the definition and in the renderer, at every index. The defaults stay
 * explicit: the merchant-visible copy is the argument, and a slot with an empty argument ships
 * empty and is skipped by its renderer.
 */
function galleryItem(slot: number, caption: string): SettingField[] {
  return [
    { key: `item${slot}Image`, label: `Picture ${slot}`, type: "image", default: null },
    {
      key: `item${slot}Caption`,
      label: `Caption ${slot}`,
      type: "text",
      default: caption,
      ...(slot === 1
        ? {
            help: "Shown under the picture, and used as its description for screen readers. A picture with no caption is treated as decoration.",
          }
        : {}),
    },
  ];
}

function teamItem(slot: number, name: string, role: string, bio: string): SettingField[] {
  return [
    { key: `item${slot}Image`, label: `Photo ${slot}`, type: "image", default: null },
    { key: `item${slot}Name`, label: `Name ${slot}`, type: "text", default: name },
    { key: `item${slot}Role`, label: `Role ${slot}`, type: "text", default: role },
    { key: `item${slot}Bio`, label: `One line about them ${slot}`, type: "text", default: bio },
  ];
}

function cardItem(
  slot: number,
  values: { title: string; meta: string; body: string; href: string },
): SettingField[] {
  return [
    { key: `item${slot}Image`, label: `Card ${slot} picture`, type: "image", default: null },
    { key: `item${slot}Title`, label: `Card ${slot} title`, type: "text", default: values.title },
    {
      key: `item${slot}Meta`,
      label: `Card ${slot} detail line`,
      type: "text",
      default: values.meta,
      ...(slot === 1
        ? {
            help: "The small line above the title — a place and a job type for a role, a client and a year for a case study.",
          }
        : {}),
    },
    { key: `item${slot}Body`, label: `Card ${slot} summary`, type: "textarea", default: values.body },
    { key: `item${slot}Href`, label: `Card ${slot} link`, type: "text", default: values.href },
  ];
}

function credentialItem(slot: number, mark: string, label: string, issuer: string): SettingField[] {
  return [
    {
      key: `item${slot}Image`,
      label: `Mark ${slot}`,
      type: "image",
      default: null,
      ...(slot === 1
        ? {
            help: "Your own certification mark, if you have the artwork. Leave it empty and the short code below is set as a wordmark instead.",
          }
        : {}),
    },
    { key: `item${slot}Mark`, label: `Short code ${slot}`, type: "text", default: mark },
    { key: `item${slot}Label`, label: `What it certifies ${slot}`, type: "text", default: label },
    { key: `item${slot}Issuer`, label: `Issued by ${slot}`, type: "text", default: issuer },
  ];
}

function videoItem(
  slot: number,
  values: { title: string; meta: string; href: string },
): SettingField[] {
  return [
    {
      key: `item${slot}Poster`,
      label: `Video ${slot} poster`,
      type: "image",
      default: null,
      ...(slot === 1
        ? {
            help: "Your own still, uploaded here. Nothing is ever fetched from the video host before someone presses play.",
          }
        : {}),
    },
    { key: `item${slot}Title`, label: `Video ${slot} title`, type: "text", default: values.title },
    { key: `item${slot}Meta`, label: `Video ${slot} detail line`, type: "text", default: values.meta },
    {
      key: `item${slot}Href`,
      label: `Video ${slot} page`,
      type: "text",
      default: values.href,
      ...(slot === 1
        ? {
            help: "Where the video lives. Used when there is no embed address, and shown as a way out of the page either way.",
          }
        : {}),
    },
    {
      key: `item${slot}Embed`,
      label: `Video ${slot} embed address`,
      type: "text",
      default: "",
      ...(slot === 1
        ? {
            help: "Optional. Paste what the host's Share → Embed panel gives you, https only. Nothing loads from it until a visitor presses play.",
          }
        : {}),
    },
  ];
}

export const CMS_SECTION_DEFS: SectionDef[] = [
  {
    /*
     * The interior page title. Every one of the thirty-five pages opens with this.
     *
     * `hero` cannot do this job: it is a two-column band with a plate, two calls to action and a
     * minimum height, which is a front page and not a Returns Policy. This is a title, a line of
     * context and nothing else.
     *
     * `rank` is the field that matters. See `render/PageHeader.tsx` — a section cannot see the page
     * it is on, so which heading rank it takes is a decision the merchant makes, with a default
     * that is right for the pages this block exists for.
     */
    type: "page-header",
    name: "Page header",
    group: "content",
    // Six rows, not five: five is 200px, and the default eyebrow, title and standfirst measure
    // about 215 at a desktop canvas. A block that does not fit its own defaults is a block every
    // merchant has to resize before they have typed anything.
    defaultLayout: { w: 12, h: 6 },
    fields: [
      { key: "eyebrow", label: "Eyebrow", type: "text", default: "Company" },
      { key: "heading", label: "Page title", type: "text", default: "About us" },
      {
        key: "standfirst",
        label: "Standfirst",
        type: "textarea",
        default:
          "Thirty years of supplying the people who keep plants running — and the reasons they keep coming back.",
        help: "One or two sentences under the title. Blank lines start a new paragraph.",
      },
      {
        key: "rank",
        label: "Heading level",
        type: "select",
        options: [
          { value: "page", label: "Page title (h1)" },
          { value: "section", label: "Section heading (h2)" },
        ],
        default: "page",
        help: "Leave this as the page title. Switch it to a section heading only if the page also has a hero — a page may have exactly one page title.",
      },
      { key: "align", label: "Alignment", type: "select", options: ALIGN_OPTIONS, default: "left" },
      { key: "tone", label: "Ground", type: "select", options: GROUND_OPTIONS, default: "surface" },
      {
        key: "tint",
        label: "Ground tint",
        type: "color",
        default: null,
        help: "Leave empty to use the ground above. A brand surface colour — the words on it stay the theme's text.",
      },
      bleedField("Span the page edge to edge. The title still lines up with the content column."),
    ],
  },
  {
    /*
     * Structured prose from one authored field — the section that covers the nine legal pages and
     * most of the support ones on its own.
     *
     * The convention (`## ` for a subheading, `- ` for a list item, `1. ` for a clause, a blank
     * line for a paragraph) is parsed in `render/prose.ts` and rendered as real `<h2>`, `<p>`,
     * `<ul>` and `<ol>`. No markdown dependency and no `dangerouslySetInnerHTML`.
     */
    type: "long-form",
    name: "Long-form text",
    group: "content",
    defaultLayout: { w: 8, h: 18 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "" },
      {
        key: "updated",
        label: "Last updated line",
        type: "text",
        default: "Last updated 1 April 2026",
      },
      {
        key: "body",
        label: "Body",
        type: "textarea",
        default: [
          "This policy explains what we do with the details you give us when you order, ask for a quote or write to us.",
          "",
          "## What we collect",
          "",
          "Only what an order needs:",
          "",
          "- Your name, company, phone number and email address",
          "- The delivery address and, where you give one, the GSTIN",
          "- What you ordered, and what we invoiced for it",
          "",
          "## What we never do",
          "",
          "We do not sell your details, and we do not pass them to anyone who is not carrying out part of your order — a courier needs the address, and nobody else does.",
          "",
          "## How long we keep it",
          "",
          "Invoices and the records attached to them are kept for eight years, which is what the tax rules require. Enquiries that did not become orders are removed after two years.",
          "",
          "## Asking us to change or remove something",
          "",
          "Write to the address on the contact page and we will reply within thirty days.",
        ].join("\n"),
        help: "Start a line with ## for a subheading, with - for a bullet, with 1. for a numbered clause. A blank line starts a new paragraph. Nothing else is treated as formatting.",
      },
      {
        key: "showToc",
        label: "Show a table of contents",
        type: "toggle",
        default: false,
        help: "Lists every ## subheading and jumps to it. Worth it on a long Terms page, noise on a short one.",
      },
      { key: "tocLabel", label: "Table of contents label", type: "text", default: "On this page" },
      {
        key: "measure",
        label: "Reading width",
        type: "select",
        options: [
          { value: "comfortable", label: "Comfortable (about 70 characters)" },
          { value: "wide", label: "Wide (about 85 characters)" },
          { value: "full", label: "The full column" },
        ],
        default: "comfortable",
        help: "Long prose set across a full desktop column is hard to read back to the start of the next line.",
      },
    ],
  },
  {
    /*
     * A grid of pictures with captions: Gallery, Infrastructure, Projects.
     *
     * Twelve slots, not eight. Twelve is the count that divides evenly by the column counts this
     * section offers — 2, 3 and 4 — so a full gallery never ends in a ragged row of one, and a
     * Gallery page is precisely the page that wants more than eight pictures. The cost is held down
     * by keeping a slot to two fields.
     *
     * No lightbox. That is an interaction — focus trapping, a keyboard escape, a dialog — and this
     * pack cannot test it properly. A picture here is a picture.
     */
    type: "media-gallery",
    name: "Picture gallery",
    group: "content",
    defaultLayout: { w: 12, h: 12 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Inside the works" },
      { key: "columns", label: "Columns", type: "number", default: 3, help: "2 to 4." },
      {
        key: "mobileColumns",
        label: "Columns on a phone",
        type: "number",
        default: 2,
        help: "1 to 3.",
      },
      {
        key: "aspect",
        label: "Picture shape",
        type: "select",
        options: PICTURE_SHAPE_OPTIONS,
        default: "landscape",
      },
      ...galleryItem(1, "Bar stock, cut and staged for the week"),
      ...galleryItem(2, "The press shop, second shift"),
      ...galleryItem(3, "Goods inward, under inspection"),
      ...galleryItem(4, "Packing and labelling before dispatch"),
      ...galleryItem(5, "The loading bay at 6am"),
      ...galleryItem(6, "Test rigs in the QA room"),
      ...galleryItem(7, ""),
      ...galleryItem(8, ""),
      ...galleryItem(9, ""),
      ...galleryItem(10, ""),
      ...galleryItem(11, ""),
      ...galleryItem(12, ""),
    ],
  },
  {
    /*
     * People: Our Team, leadership, "meet the experts".
     *
     * Six slots. Six covers a leadership row of three and a departmental row of six, and divides by
     * both column counts. A directory longer than that is a list of records and wants a catalogue
     * behind it, not twenty-four more settings controls.
     */
    type: "team-grid",
    name: "Team",
    group: "content",
    defaultLayout: { w: 12, h: 10 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "The people you will deal with" },
      { key: "columns", label: "Columns", type: "number", default: 3, help: "2 to 4." },
      { key: "mobileColumns", label: "Columns on a phone", type: "number", default: 2, help: "1 or 2." },
      {
        key: "shape",
        label: "Photo shape",
        type: "select",
        options: PORTRAIT_SHAPE_OPTIONS,
        default: "square",
      },
      ...teamItem(
        1,
        "Anil Deshmukh",
        "Managing Director",
        "Started on the shop floor in 1994 and still signs off every rate contract.",
      ),
      ...teamItem(
        2,
        "Farida Kapadia",
        "Head of Quality",
        "Runs the test lab and the certificate trail that travels with every consignment.",
      ),
      ...teamItem(
        3,
        "Suresh Iyer",
        "Regional Manager, South",
        "Twelve years with the same forty accounts, most of them by name.",
      ),
      ...teamItem(4, "", "", ""),
      ...teamItem(5, "", "", ""),
      ...teamItem(6, "", "", ""),
    ],
  },
  {
    /*
     * Anything article-shaped: Case Studies, Projects, Careers, Press.
     *
     * A role on a Careers page is a card with a place and a contract type on it, so this section
     * carries a generic `detail line` rather than the library growing a jobs section that differs
     * from this one by two words and a heading. The same line reads "Nashik · Full time" on a
     * Careers page and "Tata Projects · 2025" on a case study.
     */
    type: "card-deck",
    name: "Card deck",
    group: "content",
    defaultLayout: { w: 12, h: 12 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Recent work" },
      { key: "columns", label: "Columns", type: "number", default: 3, help: "2 to 4." },
      { key: "mobileColumns", label: "Columns on a phone", type: "number", default: 1, help: "1 or 2." },
      {
        key: "aspect",
        label: "Picture shape",
        type: "select",
        options: [{ value: "none", label: "No picture" }, ...PICTURE_SHAPE_OPTIONS],
        default: "wide",
        help: "Choose no picture for a list of roles or press mentions, where a photograph is not the point.",
      },
      {
        key: "linkLabel",
        label: "Link wording",
        type: "text",
        default: "Read more",
        help: "Shown at the foot of every card that has a link. Leave empty to show nothing — the whole card is the link either way.",
      },
      ...cardItem(1, {
        title: "Fastener supply for a 400-bed hospital fit-out",
        meta: "Shapoorji Pallonji · 2025",
        body: "Eleven thousand line items across nine months, consolidated into one weekly delivery and one monthly invoice.",
        href: "",
      }),
      ...cardItem(2, {
        title: "Rate contract for a three-plant OEM",
        meta: "Deccan Infra · 2024",
        body: "Held pricing for eighteen months with a quarterly review, and cut their raising-a-PO time from four days to one.",
        href: "",
      }),
      ...cardItem(3, {
        title: "Emergency shutdown supply, 36 hours",
        meta: "Thermax · 2024",
        body: "A gasket set nobody stocked, sourced, tested and on site before the next shift started.",
        href: "",
      }),
      ...cardItem(4, { title: "", meta: "", body: "", href: "" }),
      ...cardItem(5, { title: "", meta: "", body: "", href: "" }),
      ...cardItem(6, { title: "", meta: "", body: "", href: "" }),
    ],
  },
  {
    /*
     * Certifications, standards and awards — the Certifications and Quality pages.
     *
     * Deliberately **not** an option on `logo-wall`, and not a duplicate of it either. `logo-wall`
     * is one textarea of *customer* names set as wordmarks, and its argument against uploaded
     * artwork is a licensing one: a merchant rarely has permission to show their customer's mark.
     * A certification is the merchant's *own* — they hold the certificate — so a mark is allowed
     * here, and each entry carries three facts rather than one: what it is, what it certifies, and
     * who issued it. That is a different section, not a flag on an existing one.
     */
    type: "credential-strip",
    name: "Certifications",
    group: "social",
    defaultLayout: { w: 12, h: 6 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Certified to" },
      { key: "columns", label: "Columns", type: "number", default: 3, help: "2 to 4." },
      { key: "mobileColumns", label: "Columns on a phone", type: "number", default: 1, help: "1 or 2." },
      ...credentialItem(1, "ISO 9001", "Quality management system", "TÜV SÜD · valid to March 2027"),
      ...credentialItem(2, "ISO 14001", "Environmental management", "TÜV SÜD · valid to March 2027"),
      ...credentialItem(3, "IS 1367", "Fastener conformity", "BIS licence CM/L-7714302"),
      ...credentialItem(4, "", "", ""),
      ...credentialItem(5, "", "", ""),
      ...credentialItem(6, "", "", ""),
    ],
  },
  {
    /*
     * The Videos page. See `render/VideoList.tsx` for the argument — the decision this section
     * encodes is that **nothing third-party loads until a visitor asks for it**, and that the
     * poster is the merchant's own still rather than something fetched from the host.
     *
     * Four slots. A page with more than four films is a video library, and a library is records
     * with a search box, not twenty more settings controls.
     */
    type: "video",
    name: "Video",
    group: "content",
    defaultLayout: { w: 12, h: 11 },
    fields: [
      { key: "heading", label: "Heading", type: "text", default: "Watch the works" },
      { key: "columns", label: "Columns", type: "number", default: 2, help: "1 to 3." },
      {
        key: "playLabel",
        label: "Play wording",
        type: "text",
        default: "Play",
        help: "The title is added to it, so a screen reader hears \"Play, A day on the line\" rather than twelve buttons called Play.",
      },
      {
        key: "watchLabel",
        label: "Wording for the link out",
        type: "text",
        default: "Watch on the host's site",
      },
      {
        key: "privacyNote",
        label: "Privacy line",
        type: "text",
        default: "Pressing play loads the film from the host, which may set its own cookies.",
        help: "Shown under any video that has an embed address. Nothing loads before the press; this line says so.",
      },
      ...videoItem(1, {
        title: "A day on the line",
        meta: "4 min · Factory tour",
        href: "",
      }),
      ...videoItem(2, {
        title: "How we test every batch",
        meta: "2 min · Quality",
        href: "",
      }),
      ...videoItem(3, { title: "", meta: "", href: "" }),
      ...videoItem(4, { title: "", meta: "", href: "" }),
    ],
  },
];
