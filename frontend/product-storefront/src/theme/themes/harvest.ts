/**
 * Harvest — agri inputs.
 *
 * The airiest theme: a pale green ground, wide radius, the largest spacing step, and a centred
 * hero with room around it. Composition is trust-led — value columns, then product, then quotes
 * and an FAQ beside the contact block — because a dealer buying nutrition is buying advice as much
 * as stock, and the questions come before the cart.
 */

import type { Theme } from "../schema";
import { page, sec } from "./build";

export const harvest: Theme = {
  key: "harvest",
  name: "Harvest",
  description:
    "Open and reassuring. Pale green, wide corners, the biggest spacing step, and answers placed before the order form.",
  industry: "Agri inputs",
  tokens: {
    colorBg: "#F3F7F0",
    colorSurface: "#FFFFFF",
    colorText: "#152314",
    colorMuted: "#53664F",
    colorAccent: "#1F7A3D",
    colorAccentText: "#FFFFFF",
    colorBorder: "#D5E3D0",
    fontHeading: '"Avenir Next", "Segoe UI", system-ui, sans-serif',
    fontBody: '"Avenir Next", "Segoe UI", system-ui, sans-serif',
    radius: 12,
    space: 18,
    maxWidth: 1140,
  },
  pages: [
    page("hrv-home", "/", "Home", [
      sec("hrv-hero", "hero", { x: 0, y: 0, w: 12, h: 10 }, {
        eyebrow: "For dealers and farmer producer organisations",
        heading: "Inputs that arrive before the sowing window closes",
        body: "Water-soluble nutrition, irrigation and crop protection, stocked district-wise and released against the season.",
        ctaLabel: "See the season's stock",
        secondaryLabel: "Become a dealer",
        tone: "surface",
        align: "center",
      }),
      sec("hrv-features", "feature-columns", { x: 0, y: 10, w: 12, h: 6 }, {
        heading: "What a dealership gets",
        columns: 3,
        item1Title: "District-wise stock",
        item1Body: "Held in the district, not in a state warehouse four days away from your sowing window.",
        item2Title: "Season credit",
        item2Body: "Terms that end with the harvest, not thirty days after the invoice.",
        item3Title: "Agronomy support",
        item3Body: "A field agronomist visits your territory monthly and takes farmer questions with you.",
      }),
      sec("hrv-grid", "product-grid", { x: 0, y: 16, w: 12, h: 12 }, {
        heading: "This season's stock",
        source: "category:agri",
        limit: 6,
        columns: 3,
        priceNote: "inclusive",
      }),
      sec("hrv-quotes", "testimonials", { x: 0, y: 28, w: 12, h: 7 }, {
        heading: "From the territory",
        quote1: "The kharif stock landed a week before the rains. That has never happened with anyone else.",
        author1: "Bhaskar Patil",
        role1: "Dealer, Sangli district",
        quote2: "Their agronomist sat with forty of my farmers in one afternoon. My sales doubled that season.",
        author2: "Amrita Devi",
        role2: "FPO Secretary, Muzaffarpur",
      }),
      sec("hrv-faq", "faq", { x: 0, y: 35, w: 7, h: 9 }, {
        heading: "Dealer questions",
        q1: "What is the credit period?",
        a1: "Season credit: kharif dues clear by November, rabi by April. No interest inside the season.",
        q2: "Do you take back unsold stock?",
        a2: "Sealed bags within the season, yes, at full credit. Opened or weathered stock, no.",
        q3: "Is there territory protection?",
        a3: "One appointed dealer per taluka for the nutrition range. Irrigation is open territory.",
      }),
      sec("hrv-contact", "contact-block", { x: 7, y: 35, w: 5, h: 9 }, {
        heading: "Dealer desk",
        address: "Survey 118, Pune–Solapur Road\nYavat, Maharashtra 412214",
        phone: "+91 2117 274 500",
        email: "dealers@example.co.in",
        hours: "Mon–Sat, 8:00am–7:00pm IST",
      }),
      sec("hrv-cta", "footer-cta", { x: 0, y: 44, w: 12, h: 6 }, {
        heading: "Apply for a dealership",
        body: "Territory, credit line and an agronomist, settled in one visit.",
        ctaLabel: "Apply for a dealership",
        tone: "accent",
      }),
    ]),
    page("hrv-catalogue", "/catalogue", "Catalogue", [
      sec("hrv-cat-hero", "hero", { x: 0, y: 0, w: 12, h: 6 }, {
        eyebrow: "Catalogue",
        heading: "Nutrition, irrigation and protection",
        body: "Prices are dealer prices, inclusive of GST, ex-district warehouse.",
        ctaLabel: "",
        secondaryLabel: "",
        tone: "surface",
        align: "center",
      }),
      sec("hrv-cat-tiles", "category-tiles", { x: 0, y: 6, w: 12, h: 7 }, {
        heading: "By season need",
        columns: 2,
        limit: 4,
      }),
      sec("hrv-cat-grid", "product-grid", { x: 0, y: 13, w: 12, h: 12 }, {
        heading: "Everything in stock",
        source: "all",
        limit: 9,
        columns: 3,
        priceNote: "inclusive",
      }),
      sec("hrv-cat-note", "rich-text", { x: 0, y: 25, w: 12, h: 5 }, {
        heading: "On counterfeit inputs",
        body: "Every bag we sell carries a scratch code that a farmer can verify by SMS before opening it. If the code does not verify, do not sell the bag — call us and we will replace the consignment and trace where it entered the chain.\nThis costs us about ninety paise a bag and it is the single most useful thing we do for the people at the end of this supply chain.",
        align: "center",
      }),
      sec("hrv-cat-cta", "footer-cta", { x: 0, y: 30, w: 12, h: 6 }, {
        heading: "Planning the next season?",
        body: "Send your indent by the 15th and we will hold it in the district warehouse.",
        ctaLabel: "Send an indent",
        tone: "contrast",
      }),
    ]),
    page("hrv-product", "/product", "Line detail", [
      sec("hrv-prd-hero", "hero", { x: 0, y: 0, w: 12, h: 7 }, {
        eyebrow: "Agri inputs",
        heading: "NPK 19:19:19, water soluble",
        body: "Twenty-five kilogram bags, fertigation grade, fully soluble with no residue in the filter.",
        ctaLabel: "Add to your indent",
        secondaryLabel: "See the analysis",
        tone: "surface",
      }),
      sec("hrv-prd-plate", "image-with-text", { x: 0, y: 7, w: 12, h: 8 }, {
        heading: "Tested for solubility, not just for analysis",
        body: "Every batch is dissolved and filtered before it leaves. An input that passes its NPK analysis and then blocks a drip line has failed the only test that matters on a farm.",
        caption: "Yavat blending unit",
        imageSide: "left",
      }),
      sec("hrv-prd-prices", "price-table", { x: 0, y: 15, w: 7, h: 10 }, {
        heading: "Grades and dealer rates",
        source: "category:agri",
        limit: 4,
        priceNote: "inclusive",
        note: "Dealer rates, inclusive of GST, ex-district warehouse. Season credit applies.",
      }),
      sec("hrv-prd-enquiry", "enquiry-form", { x: 7, y: 15, w: 5, h: 12 }, {
        heading: "Send your indent",
        body: "Grade, quantity and the district you want it held in.",
        submitLabel: "Send the indent",
        successMessage: "Indent received. The dealer desk will confirm your allocation by phone.",
      }),
      sec("hrv-prd-quotes", "testimonials", { x: 0, y: 27, w: 12, h: 7 }, {
        heading: "Dealers on this line",
        quote1: "No filter blockages in two seasons. My drip farmers ask for it by name now.",
        author1: "Sudhir Kale",
        role1: "Dealer, Baramati",
        quote2: "The scratch code ended the counterfeit argument in my shop for good.",
        author2: "Imran Qureshi",
        role2: "Dealer, Nashik",
      }),
      sec("hrv-prd-cta", "footer-cta", { x: 0, y: 34, w: 12, h: 6 }, {
        heading: "Want an agronomist in your taluka?",
        body: "We run farmer meetings with appointed dealers every month.",
        ctaLabel: "Book a farmer meeting",
        tone: "accent",
      }),
    ]),
  ],
};
