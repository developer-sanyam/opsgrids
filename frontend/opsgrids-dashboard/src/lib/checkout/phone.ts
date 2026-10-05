/** An Indian mobile number, as the provider takes it: ten digits, after any `+91` / `0` prefix. */
export function normalisePhone(input: string): string | null {
  const digits = input.replace(/[\s()-]/g, "").replace(/^(\+91|0091|0)/, "");
  return /^[6-9]\d{9}$/.test(digits) ? digits : null;
}
