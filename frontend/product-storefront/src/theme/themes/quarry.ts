/**
 * Quarry — building materials.
 *
 * The editorial theme: a warm sand ground, a serif set, zero radius, and generous space. The home
 * page reads like a brochure — hero, a full-width plate with copy, two columns of prose — and only
 * then shows product, because a site engineer choosing cement is choosing a supplier's judgement.
 */

import type { Theme } from "../schema";
import { page, sec } from "./build";

export const quarry: Theme = {
  key: "quarry",
  name: "Quarry",
  description:
    "Editorial and unhurried. Warm sand, a serif set, square corners and a brochure-shaped home page that earns the product grid.",
  industry: "Building materials",
  tokens: {
    colorBg: "#F5F0E6",
    colorSurface: "#FFFFFF",
    colorText: "#241E16",
    colorMuted: "#665B4D",
    colorAccent: "#8A4B22",
    colorAccentText: "#FFFFFF",
    colorBorder: "#E0D5C2",
    fontHeading: '"Iowan Old Style", Georgia, "Times New Roman", serif',
    fontBody: 'Georgia, "Times New Roman", serif',
    radius: 0,
    space: 18,
    maxWidth: 1180,
  },
  pages: [
    page("qry-home", "/", "Home", [
      sec("qry-hero", "hero", { x: 0, y: 0, w: 12, h: 10 }, {
        eyebrow: "Aggregates, cement and binders",
        heading: "Materials that behave the same on the ninth pour as on the first",
        body: "Batch-consistent cement, admixtures and waterproofing, delivered to the site gate on a schedule you set.",
        ctaLabel: "See what we carry",
        secondaryLabel: "Request a site visit",
        tone: "surface",
        align: "center",
      }),
      sec("qry-plate", "image-with-text", { x: 0, y: 10, w: 12, h: 8 }, {
        heading: "One silo, one grade, one batch number",
        body: "We do not blend across batches to make up a load. If a consignment needs two batches you get two delivery notes and two certificates, and your QA team gets to make the decision instead of discovering it later.",
        caption: "Chakan bulk terminal",
        imageSide: "left",
        ctaLabel: "How we test",
      }),
      sec("qry-prose", "rich-text", { x: 2, y: 18, w: 8, h: 6 }, {
        heading: "Thirty years of the same delivery window",
        body: "We started in 1996 with one tipper and a rate contract for a single housing project in Pune.\nThe fleet is larger now and the terminals are three, but the promise has not moved: a scheduled delivery arrives inside its window or you hear from us before it does not.\nThat is the whole pitch. Everything else in this catalogue is detail.",
        align: "center",
        columns: 2,
      }),
      sec("qry-grid", "product-grid", { x: 0, y: 24, w: 12, h: 12 }, {
        heading: "Cement, binders and protection",
        source: "category:cement",
        limit: 6,
        columns: 3,
      }),
      sec("qry-quotes", "testimonials", { x: 0, y: 36, w: 12, h: 7 }, {
        heading: "From the site office",
        quote1: "Eleven deliveries on the Hinjewadi job, eleven inside the window. The site never had to re-plan a pour.",
        author1: "Sandeep Gokhale",
        role1: "Project Manager, Vastu Constructions",
        quote2: "Their QA sends the batch certificate before the truck arrives. Nobody else does that.",
        author2: "Fatima Sheikh",
        role2: "Quality Head, Meridian Infra",
        quote3: "Rates held through a monsoon that moved everyone else's twice.",
        author3: "R. Venkatesan",
        role3: "Purchase, Coastal Builders",
      }),
      sec("qry-cta", "footer-cta", { x: 0, y: 43, w: 12, h: 6 }, {
        heading: "Planning a pour schedule?",
        body: "Send us the programme and we will hold the stock against it.",
        ctaLabel: "Share your programme",
        tone: "contrast",
      }),
    ]),
    page("qry-catalogue", "/catalogue", "Catalogue", [
      sec("qry-cat-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Catalogue",
        heading: "What we carry, and what it is for",
        body: "Every line here is stocked at all three terminals unless the lead time says otherwise.",
        ctaLabel: "",
        secondaryLabel: "",
        tone: "surface",
        align: "center",
      }),
      sec("qry-cat-tiles", "category-tiles", { x: 0, y: 6, w: 12, h: 7 }, {
        heading: "By trade",
        columns: 3,
        limit: 6,
      }),
      sec("qry-cat-grid", "product-grid", { x: 0, y: 13, w: 12, h: 13 }, {
        heading: "All lines",
        source: "all",
        limit: 9,
        columns: 3,
      }),
      sec("qry-cat-faq", "faq", { x: 0, y: 26, w: 7, h: 9 }, {
        heading: "Delivery and storage",
        q1: "How long can bagged cement be stored on site?",
        a1: "Ninety days off the ground on a covered pallet. We date-stamp every bag so you can rotate properly.",
        q2: "Do you deliver on Sundays?",
        a2: "For scheduled pours, yes, with 48 hours' notice. There is no Sunday surcharge on contract rates.",
        q3: "What happens if a load is rejected at the gate?",
        a3: "It returns to the terminal at our cost and a replacement is scheduled for the next window. You are not invoiced for a rejected load.",
      }),
      sec("qry-cat-contact", "contact-block", { x: 7, y: 26, w: 5, h: 9 }, {
        heading: "Terminal despatch",
        address: "Chakan Bulk Terminal\nPlot 7, MIDC Phase III\nPune, Maharashtra 410501",
        phone: "+91 20 6720 4400",
        email: "despatch@example.co.in",
        hours: "Mon–Sat, 6:00am–8:00pm IST",
      }),
      sec("qry-cat-cta", "footer-cta", { x: 0, y: 35, w: 12, h: 6 }, {
        heading: "Rate contract for the financial year",
        body: "Quarterly review, no mid-quarter revision. That is the deal.",
        ctaLabel: "Ask for the terms",
        tone: "accent",
      }),
    ]),
    page("qry-product", "/product", "Line detail", [
      sec("qry-prd-hero", "hero", { x: 0, y: 0, w: 12, h: 7 }, {
        eyebrow: "Cement & binders",
        heading: "OPC 53 grade, IS 269:2015",
        body: "Fifty-kilogram bags, batch certified, from the Chakan terminal.",
        ctaLabel: "Request a delivery slot",
        secondaryLabel: "See the test report",
        tone: "surface",
      }),
      sec("qry-prd-plate", "image-with-text", { x: 0, y: 7, w: 12, h: 8 }, {
        heading: "Tested at the terminal, not at the mill",
        body: "Every batch is re-tested for compressive strength and setting time when it lands at our terminal. The mill's certificate and ours both travel with the load.",
        caption: "Terminal laboratory, Chakan",
        imageSide: "right",
      }),
      sec("qry-prd-prices", "price-table", { x: 0, y: 15, w: 7, h: 10 }, {
        heading: "Grades and rates",
        source: "category:cement",
        limit: 5,
        note: "Ex-terminal. Site delivery quoted per kilometre band.",
      }),
      sec("qry-prd-enquiry", "enquiry-form", { x: 7, y: 15, w: 5, h: 12 }, {
        heading: "Book against a pour",
        body: "Tell us the date and the quantity and we will confirm the slot.",
        submitLabel: "Request the slot",
        successMessage: "Request received. Despatch will confirm your slot by phone.",
      }),
      sec("qry-prd-note", "rich-text", { x: 0, y: 27, w: 12, h: 5 }, {
        heading: "A note on substitution",
        body: "If a grade is unavailable we will offer the nearest specification in writing and wait for your QA to accept it. We do not substitute silently, even when the schedule is tight — a pour made with a material nobody approved is a problem that outlives the delay it avoided.",
        align: "left",
        columns: 2,
      }),
      sec("qry-prd-cta", "footer-cta", { x: 0, y: 32, w: 12, h: 6 }, {
        heading: "Talk to the technical desk",
        body: "Mix design questions, admixture dosage, curing in a Konkan monsoon — ask.",
        ctaLabel: "Ask the technical desk",
        tone: "accent",
      }),
    ]),
  ],
};
