/**
 * One CSV field, safe to open in a spreadsheet.
 *
 * RFC 4180 quoting alone is not enough: Excel and Sheets run a cell that starts with `=`, `+`,
 * `-`, `@`, tab or CR as a formula, and exported rows include text anonymous visitors typed
 * (storefront enquiries, form submissions). A leading `'` makes the spreadsheet show it as text.
 * A plain number — `-12.50`, `-1,20,000` — cannot be a formula and is left alone so it still sums.
 * Only a well-formed number qualifies: digits, optional group commas, one optional fraction.
 * `-,` or `-1.2.3` is not a number and is guarded like any other text.
 */
const SIGNED_NUMBER = /^-?\d[\d,]*(?:\.\d+)?$/;

/** The formula guard alone, without CSV quoting: prefix `'` unless the cell is a plain number. */
export function guardCsvCell(value: string): string {
  return /^[=+\-@\t\r]/.test(value) && !SIGNED_NUMBER.test(value) ? `'${value}` : value;
}

export function csvCell(value: string): string {
  const safe = guardCsvCell(value);
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}
