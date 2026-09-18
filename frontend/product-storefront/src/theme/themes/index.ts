/**
 * The shipped theme library.
 *
 * A theme is a starting point, not a dependency: adopting one deep-copies its tokens and pages
 * into the merchant's `StoreDoc` and the link is cut (see `adoptTheme`). Editing a theme here
 * therefore changes what *new* stores start from and nothing about stores that already exist.
 */

import type { StoreDoc, Theme } from "../schema";
import { carton } from "./carton";
import { circuit } from "./circuit";
import { depot } from "./depot";
import { foundry } from "./foundry";
import { harvest } from "./harvest";
import { quarry } from "./quarry";

export const THEMES: Theme[] = [foundry, depot, quarry, circuit, carton, harvest];

export function themeByKey(key: string): Theme | null {
  return THEMES.find((theme) => theme.key === key) ?? null;
}

/**
 * The brand a theme suggests when it is adopted from the gallery.
 *
 * It lives beside the themes rather than on `Theme`, because a brand is the merchant's and the
 * schema is deliberate about that: a `StoreDoc` carries `brand`, a `Theme` does not. This map is
 * only a first draft the merchant overwrites.
 */
export const SUGGESTED_BRAND: Record<string, StoreDoc["brand"]> = {
  foundry: {
    name: "Shakti Industrial Supply",
    tagline: "Fasteners, bearings and power transmission since 1994",
    logoText: "SIS",
  },
  depot: {
    name: "Meridian Trade Depot",
    tagline: "Eleven categories, one despatch bay",
    logoText: "MTD",
  },
  quarry: {
    name: "Deccan Materials",
    tagline: "Cement, binders and aggregates for the site gate",
    logoText: "DM",
  },
  circuit: {
    name: "Ampere Components",
    tagline: "Switchgear and cable for panel builders",
    logoText: "AMP",
  },
  carton: {
    name: "Vasai Packaging Works",
    tagline: "Corrugation, strapping and despatch consumables",
    logoText: "VPW",
  },
  harvest: {
    name: "Yavat Agri Inputs",
    tagline: "Nutrition and irrigation, district-wise",
    logoText: "YAI",
  },
};

export function suggestedBrand(themeKey: string): StoreDoc["brand"] {
  return (
    SUGGESTED_BRAND[themeKey] ?? {
      name: "Your company",
      tagline: "Tell buyers what you supply",
      logoText: "CO",
    }
  );
}

export { carton, circuit, depot, foundry, harvest, quarry };
