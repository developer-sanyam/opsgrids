/**
 * Rupees-to-paise parsing for admin-entered money (the "Set price" dialog). String parsing only —
 * an operator-typed rupee amount is never routed through `Number` for the conversion itself, which
 * cannot represent most two-decimal rupee amounts exactly (0.1 + 0.2 problems). Money is an integer
 * minor amount with an ISO currency, never a float (ADR-0000 §8) — this is the one place a typed
 * rupee string becomes that integer.
 */

export type ParsedRupees = { ok: true; minor: number } | { ok: false; error: string };

/** Whole rupees, or rupees with one or two decimal places. Nothing else matches. */
const PATTERN = /^(\d+)(?:\.(\d{1,2}))?$/;

/** More than two decimal places — its own message, since "not a number" would be misleading. */
const TOO_MANY_DECIMALS = /^\d+\.\d{3,}$/;

/** The ceiling the admin-pricing contract itself sets: 10^10 paise (₹10,00,00,000/mo). Anything
 *  above this is almost certainly a typo, not a real price. */
export const MAX_AMOUNT_MINOR = 10_000_000_000;

/**
 * Parse a rupee string ("2499.50") into exact integer paise (249950). Rejects empty input,
 * negatives, more than two decimal places, non-numeric text, zero, and amounts above the
 * contract's ceiling. The only arithmetic is `BigInt` on the two textual halves — no `parseFloat`,
 * no `Number(x) * 100`.
 */
export function parseRupeesToPaise(raw: string): ParsedRupees {
  const trimmed = raw.trim();
  if (trimmed === "") return { ok: false, error: "Enter an amount." };
  if (trimmed.startsWith("-")) return { ok: false, error: "Enter a positive amount." };

  const match = PATTERN.exec(trimmed);
  if (!match) {
    if (TOO_MANY_DECIMALS.test(trimmed)) {
      return { ok: false, error: "Enter at most 2 decimal places." };
    }
    return { ok: false, error: "Enter a valid amount, e.g. 2499.50." };
  }

  // The first group always matches when the whole pattern does — the `"0"` fallback only
  // satisfies `noUncheckedIndexedAccess`'s inability to know that statically.
  const [, rupeesPart = "0", paisePart = ""] = match;
  const paddedPaise = `${paisePart}00`.slice(0, 2);
  const minor = BigInt(rupeesPart) * 100n + BigInt(paddedPaise);

  if (minor <= 0n) return { ok: false, error: "Enter an amount greater than zero." };
  if (minor > BigInt(MAX_AMOUNT_MINOR)) {
    return { ok: false, error: "That amount looks too large — check it and try again." };
  }
  return { ok: true, minor: Number(minor) };
}
