/**
 * Money, out to a sheet. Amounts are integer minor units with an ISO currency everywhere but the
 * digits in a cell, and the number of decimals is the currency's own (`platform_core/money.py`):
 * none for JPY, KRW, VND, CLP and ISK, three for BHD, KWD, OMR, TND and JOD, two otherwise. The
 * conversion is string arithmetic, never a float, so 2934 is "29.34" and not 29.339999999999996.
 */

const NO_DECIMALS = new Set(["JPY", "KRW", "VND", "CLP", "ISK"]);
const THREE_DECIMALS = new Set(["BHD", "KWD", "OMR", "TND", "JOD"]);

function decimalsOf(currency: string): number {
  const code = currency.toUpperCase();
  if (NO_DECIMALS.has(code)) return 0;
  return THREE_DECIMALS.has(code) ? 3 : 2;
}

/** `2950` in INR is `"29.50"`; `2950` in JPY is `"2950"`; `-5` in KWD is `"-0.005"`. */
export function minorToMajor(minor: number, currency: string): string {
  const decimals = decimalsOf(currency);
  const sign = minor < 0 ? "-" : "";
  const digits = String(Math.abs(minor)).padStart(decimals + 1, "0");
  if (decimals === 0) return `${sign}${digits}`;
  return `${sign}${digits.slice(0, -decimals)}.${digits.slice(-decimals)}`;
}
