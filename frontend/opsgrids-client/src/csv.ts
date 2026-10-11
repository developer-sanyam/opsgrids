/**
 * One CSV field, safe to open in a spreadsheet.
 *
 * RFC 4180 quoting alone is not enough: Excel and Sheets run a cell that starts with `=`, `+`,
 * `-`, `@`, tab or CR as a formula, and exported rows include text anonymous visitors typed
 * (storefront enquiries, form submissions). A leading `'` makes the spreadsheet show it as text.
 * A plain number — `-12.50` — cannot be a formula and is left alone so it still sums.
 */
export function csvCell(value: string): string {
  const safe = /^[=+\-@\t\r]/.test(value) && !/^-?[\d.,]+$/.test(value) ? `'${value}` : value;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}
