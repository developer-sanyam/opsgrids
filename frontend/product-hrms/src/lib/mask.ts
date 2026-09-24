/**
 * Masking for values this app shows but has no business showing whole.
 *
 * The member picker lists people by their sign-in email, which is enough to tell two "Priya"s
 * apart and is not something a screen an HR administrator leaves open should print in full. The
 * shape matches the platform's own email mask (`platform_core.pii._mask_email`): the first
 * character of each side survives and the rest — including the top-level domain — does not,
 * because a full domain beside a name is often enough to identify a person.
 */

const DOT = "•";

/** `jane@doe.com` → `j•••@d•••`. A value with no usable `@` keeps only its last two characters. */
export function maskEmail(email: string): string {
  const at = email.indexOf("@");
  if (at <= 0 || at === email.length - 1) {
    const tail = email.length > 2 ? email.slice(-2) : email;
    return DOT.repeat(Math.max(email.length - 2, 0)) + tail;
  }
  return `${email.charAt(0)}${DOT.repeat(3)}@${email.charAt(at + 1)}${DOT.repeat(3)}`;
}
