/**
 * The shipped theme library.
 *
 * A theme is a starting point, not a dependency: adopting one deep-copies its tokens and pages
 * into the merchant's `StoreDoc` and the link is cut (see `adoptTheme`). Editing a theme here
 * therefore changes what *new* stores start from and nothing about stores that already exist.
 */

import type { Brand, Theme } from "../schema";
import { atelier } from "./atelier";
import { carton } from "./carton";
import { circuit } from "./circuit";
import { depot } from "./depot";
import { foundry } from "./foundry";
import { harvest } from "./harvest";
import { quarry } from "./quarry";
import { quickcart } from "./quickcart";

export const THEMES: Theme[] = [quickcart, atelier, foundry, depot, quarry, circuit, carton, harvest];

export function themeByKey(key: string): Theme | null {
  return THEMES.find((theme) => theme.key === key) ?? null;
}

/**
 * The brand a theme suggests when it is adopted from the gallery, before a merchant has set their
 * own on the store's identity (`/settings`).
 *
 * It lives beside the themes rather than on `Theme`, because a brand is the merchant's and the
 * schema is deliberate about that: neither `StoreDoc` nor `Theme` carries one. This map is only a
 * first draft `resolveBrand`-style callers fall back to until an identity row exists.
 */
export const SUGGESTED_BRAND: Record<string, Brand> = {
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
  quickcart: {
    name: "QuickCart",
    tagline: "Groceries at the door in 11 minutes",
    logoText: "QC",
  },
  atelier: {
    name: "Atelier Handicrafts",
    tagline: "Hand-made furniture from Jodhpur since 1994",
    logoText: "ATL",
  },
};

export function suggestedBrand(themeKey: string): Brand {
  return (
    SUGGESTED_BRAND[themeKey] ?? {
      name: "Your company",
      tagline: "Tell buyers what you supply",
      logoText: "CO",
    }
  );
}

export { atelier, carton, circuit, depot, foundry, harvest, quarry, quickcart };
