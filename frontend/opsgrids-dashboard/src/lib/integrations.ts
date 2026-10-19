import catalogue from "@opsgrids/config/integrations.json";
import type { PlatformProductKey } from "@opsgrids/client";

export type ConnectorStatus = "available" | "coming_soon" | "needs_partner";

export interface Connector {
  key: string;
  name: string;
  category: string;
  description?: string;
  /** The product that owns the connect flow; `null` while no product does (Lab). */
  product: PlatformProductKey | null;
  /** The page on that product's host where the connector is connected. */
  path: string | null;
  status: ConnectorStatus;
}

export interface Category {
  key: string;
  name: string;
}

export const CATEGORIES: readonly Category[] = catalogue.categories;
export const CONNECTORS = catalogue.connectors as readonly Connector[];

/** Connectors matching the search (name or category name), grouped in category order; empty groups dropped. */
export function groupConnectors(
  query: string,
  connectors: readonly Connector[] = CONNECTORS,
  categories: readonly Category[] = CATEGORIES,
): { category: Category; connectors: Connector[] }[] {
  const needle = query.trim().toLowerCase();
  return categories
    .map((category) => ({
      category,
      connectors: connectors.filter(
        (c) =>
          c.category === category.key &&
          (needle === "" || c.name.toLowerCase().includes(needle) || category.name.toLowerCase().includes(needle)),
      ),
    }))
    .filter((group) => group.connectors.length > 0);
}
