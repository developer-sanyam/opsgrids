/**
 * Reading bundles off the wire. A bundle (`suite`) is a plan version whose `bundled_products`
 * feature names its members; a subscription to it carries those members in `entitled_products`
 * (docs/commerce-packaging.md). None of this decides access — the gateway does — it only lets a
 * screen say "included in your Suite" instead of offering to sell what the customer already has.
 */
import type { PlanVersion, Product, Subscription } from "@/api/types";
import { shortProductName } from "./productName";

/** The member keys a version declares, or an empty list for a version that is not a bundle. */
export function bundleMembers(version: PlanVersion): string[] {
  const declared = (version.features ?? []).find((f) => f.feature_key === "bundled_products");
  if (!declared || !Array.isArray(declared.value)) return [];
  return declared.value.filter((key): key is string => typeof key === "string");
}

/**
 * Bundle states that entitle their members right now. `past_due` still entitles on the server
 * (the grace window, ADR-0005), but a screen that says "included" during dunning invites the
 * customer to rely on something that ends on a date — so only a clean `active` or `trial` counts.
 */
const INCLUDING_STATES = new Set(["active", "trial"]);

/** Subscriptions *other than the product's own* that currently include it — its bundles. */
export function bundlesIncluding(productKey: string, subscriptions: Subscription[]): Subscription[] {
  return subscriptions.filter(
    (s) =>
      INCLUDING_STATES.has(s.state) &&
      s.product_key !== productKey &&
      (s.entitled_products ?? []).includes(productKey),
  );
}

/** Product keys as the dashboard names them, in the order given; unknown keys as themselves. */
export function productNames(keys: string[], products: Product[]): string[] {
  const names = new Map(products.map((p) => [p.key, shortProductName(p.name)]));
  return keys.map((key) => names.get(key) ?? key);
}

/** "People, Revenue and Finance". */
export function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1) ?? ""}`;
}
