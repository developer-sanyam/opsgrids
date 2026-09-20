/**
 * A product's name as the dashboard shows it. The catalogue calls it "OpsGrids People" because
 * that is its name out in the world and in its own app's header; inside the OpsGrids dashboard
 * the brand is already on the page, and ten cards that all begin with it read as a template.
 * A name that is not brand-prefixed ("PulseBoard") is left exactly as it is.
 */
const BRAND_PREFIX = /^OpsGrids\s+/;

export function shortProductName(name: string): string {
  const short = name.replace(BRAND_PREFIX, "").trim();
  return short === "" ? name : short;
}
