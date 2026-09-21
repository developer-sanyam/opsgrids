/**
 * Money, in and out of the catalogue's forms — the same discipline `delivery/money.ts` applies,
 * repeated here because that module is the delivery admin's own and this one is the catalogue's.
 * Integer minor units with an ISO currency everywhere except the input box; parsed from the
 * string's digits, never a float multiplied by 100.
 */

export type MoneyParse = { ok: true; minor: number } | { ok: false; message: string };

/** The value a money input shows for an amount already stored: `2950` → `"29.50"`. */
export function minorToInput(minor: number | null): string {
  if (minor === null) return "";
  const sign = minor < 0 ? "-" : "";
  const absolute = Math.abs(minor);
  const whole = Math.trunc(absolute / 100);
  const paise = absolute % 100;
  return paise === 0 ? `${sign}${whole}` : `${sign}${whole}.${String(paise).padStart(2, "0")}`;
}

/** Read a required amount, on the string. Empty is refused — unlike a delivery fee, a price is
 *  never optional. */
export function parseRequiredMinor(input: string, label: string): MoneyParse {
  const cleaned = input.replace(/[\s,₹]/g, "");
  if (cleaned === "") return { ok: false, message: `Enter ${label}.` };
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) {
    return { ok: false, message: "Enter an amount, like 199 or 199.50. No more than two decimal places." };
  }
  const whole = match[1] ?? "0";
  const fraction = (match[2] ?? "").padEnd(2, "0");
  const minor = Number(whole) * 100 + Number(fraction);
  if (!Number.isSafeInteger(minor)) return { ok: false, message: "That amount is too large." };
  return { ok: true, minor };
}

export type MoneyParseOptional = { ok: true; minor: number | null } | { ok: false; message: string };

/** Read an optional amount. Empty means "not set", never zero. */
export function parseOptionalMinor(input: string): MoneyParseOptional {
  const cleaned = input.replace(/[\s,₹]/g, "");
  if (cleaned === "") return { ok: true, minor: null };
  const result = parseRequiredMinor(input, "an amount");
  return result.ok ? { ok: true, minor: result.minor } : result;
}
