/**
 * An amount in words, Indian system (thousand, lakh, crore), for the line a GST tax invoice
 * prints under its total. Works on integer minor units and never touches a float, so the words
 * can only ever say what the figure says: `14160000` paise is exactly "One Lakh Forty-One
 * Thousand Six Hundred".
 */

const ONES = [
  "",
  "One",
  "Two",
  "Three",
  "Four",
  "Five",
  "Six",
  "Seven",
  "Eight",
  "Nine",
  "Ten",
  "Eleven",
  "Twelve",
  "Thirteen",
  "Fourteen",
  "Fifteen",
  "Sixteen",
  "Seventeen",
  "Eighteen",
  "Nineteen",
] as const;

const TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"] as const;

/** 1..99. */
function underHundred(n: number): string {
  if (n < 20) return ONES[n] ?? "";
  const tens = TENS[Math.floor(n / 10)] ?? "";
  const ones = ONES[n % 10] ?? "";
  return ones ? `${tens}-${ones}` : tens;
}

/** 1..999. */
function underThousand(n: number): string {
  const hundreds = Math.floor(n / 100);
  const rest = n % 100;
  const parts: string[] = [];
  if (hundreds > 0) parts.push(`${ONES[hundreds] ?? ""} Hundred`);
  if (rest > 0) parts.push(underHundred(rest));
  return parts.join(" ");
}

/**
 * A positive whole number in Indian grouping. Above 99 crore the crore count is itself spelled
 * out, so "123 Crore" reads "One Hundred Twenty-Three Crore" rather than inventing a unit.
 */
function indianWords(n: number): string {
  const crore = Math.floor(n / 10_000_000);
  let rest = n % 10_000_000;
  const lakh = Math.floor(rest / 100_000);
  rest %= 100_000;
  const thousand = Math.floor(rest / 1000);
  rest %= 1000;

  const parts: string[] = [];
  if (crore > 0) parts.push(`${indianWords(crore)} Crore`);
  if (lakh > 0) parts.push(`${underHundred(lakh)} Lakh`);
  if (thousand > 0) parts.push(`${underHundred(thousand)} Thousand`);
  if (rest > 0) parts.push(underThousand(rest));
  return parts.join(" ");
}

/**
 * "Indian Rupees One Lakh Forty-One Thousand Six Hundred Only" for ₹1,41,600.00;
 * "Indian Rupees Twelve and Fifty Paise Only" for ₹12.50; "Fifty Paise Only" for 50 paise;
 * "Indian Rupees Zero Only" for nothing. Rupees only — the currency this product bills in.
 *
 * @throws RangeError when `minor` is not a safe integer: words for a rounded figure would be a
 *   different amount from the one printed beside them.
 */
export function amountInWords(minor: number): string {
  if (!Number.isSafeInteger(minor)) {
    throw new RangeError("An amount in words needs a whole number of paise.");
  }
  const sign = minor < 0 ? "Minus " : "";
  const absolute = Math.abs(minor);
  const rupees = Math.floor(absolute / 100);
  const paise = absolute % 100;

  if (rupees === 0 && paise === 0) return "Indian Rupees Zero Only";

  const rupeeWords = rupees > 0 ? `Indian Rupees ${indianWords(rupees)}` : "";
  const paiseWords = paise > 0 ? `${underHundred(paise)} ${paise === 1 ? "Paisa" : "Paise"}` : "";
  const joined = rupeeWords && paiseWords ? `${rupeeWords} and ${paiseWords}` : rupeeWords || paiseWords;
  return `${sign}${joined} Only`;
}
