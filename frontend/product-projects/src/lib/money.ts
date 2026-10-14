/**
 * Money, in and out of a form — the shared implementation in `@opsgrids/ui`, re-exported so this
 * product's screens keep one local import. Integer minor units with an ISO currency everywhere
 * except the digits inside the input; the decimals are the currency's own (none for JPY, three for
 * KWD, two otherwise).
 */

export { currencyExponent as currencyDecimals, minorToInput, parseMinor } from "@opsgrids/ui";
export type { MoneyParse } from "@opsgrids/ui";
