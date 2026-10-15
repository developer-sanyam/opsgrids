/**
 * A phone number as it is read on screen.
 *
 * The server stores E.164 (`+919000000021`); a person reads `+91 90000 00021`. A masked value
 * (the server's bullets or asterisks) is not a number and is returned untouched -- it must never
 * be "tidied" into looking like one. Only India is grouped: there is no country table here, and
 * showing any other number exactly as stored beats a confidently wrong split.
 */

const E164 = /^\+[1-9]\d{7,14}$/;

/** True for the server's masked display form. */
export function isMaskedPhone(value: string): boolean {
  return /[•*]/.test(value);
}

/** `+919000000021` -> `+91 90000 00021`. Anything that is not a clean E.164 number is returned as given. */
export function formatPhone(value: string | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (isMaskedPhone(value)) return value;
  const compact = value.trim().replace(/[\s\-().]/g, "");
  if (!E164.test(compact)) return value;
  if (compact.startsWith("+91") && compact.length === 13) {
    const national = compact.slice(3);
    return `+91 ${national.slice(0, 5)} ${national.slice(5)}`;
  }
  return compact;
}
