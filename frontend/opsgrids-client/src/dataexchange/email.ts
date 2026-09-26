/**
 * An email as the server keeps it. The contact and lead services dedupe on the exact address
 * string, and pydantic's `EmailStr` lower-cases only the domain (the local part is case-sensitive
 * in principle), so an import that lower-cased the whole address would create a second record for
 * every mixed-case address it re-read from an export. This trims, keeps the local part exactly as
 * typed, lower-cases the domain, and refuses what `email-validator` refuses.
 */

const LOCAL_MAX = 64;
const LABEL_MAX = 63;
const DOMAIN_MAX = 253;
export const EMAIL_MAX = 254;

/** Top-level names that can never receive mail (RFC 6761 and friends). `example` is not one. */
const RESERVED_TLDS: ReadonlySet<string> = new Set(["test", "local", "invalid", "localhost", "onion", "arpa"]);

function isNonAsciiWord(ch: string, code: number): boolean {
  return code >= 0xa0 && !/\s/u.test(ch);
}

function isLocalCharacter(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  if (code >= 0x80) return isNonAsciiWord(ch, code);
  return /[A-Za-z0-9.!#$%&'*+\-/=?^_`{|}~]/.test(ch);
}

function isDomainCharacter(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  if (code >= 0x80) return isNonAsciiWord(ch, code);
  return /[A-Za-z0-9-]/.test(ch);
}

function validLocalPart(local: string): boolean {
  const characters = Array.from(local);
  if (characters.length === 0 || characters.length > LOCAL_MAX) return false;
  if (local.startsWith(".") || local.endsWith(".") || local.includes("..")) return false;
  return characters.every(isLocalCharacter);
}

function validDomain(domain: string): boolean {
  if (domain.length > DOMAIN_MAX) return false;
  const labels = domain.split(".");
  if (labels.length < 2) return false;
  const allValid = labels.every((label) => {
    const characters = Array.from(label);
    if (characters.length === 0 || characters.length > LABEL_MAX) return false;
    if (label.startsWith("-") || label.endsWith("-")) return false;
    return characters.every(isDomainCharacter);
  });
  if (!allValid) return false;
  const tld = labels[labels.length - 1] ?? "";
  return !/^\d+$/.test(tld) && !RESERVED_TLDS.has(tld);
}

/**
 * The address in the form the server stores, or null when the server would refuse it. The input
 * is trimmed here; nothing else about the local part changes.
 */
export function normaliseEmail(input: string): string | null {
  const text = input.trim();
  if (text.length > EMAIL_MAX) return null;
  const at = text.indexOf("@");
  if (at < 0 || at !== text.lastIndexOf("@")) return null;
  const local = text.slice(0, at);
  const domain = text.slice(at + 1).toLowerCase();
  return validLocalPart(local) && validDomain(domain) ? `${local}@${domain}` : null;
}
