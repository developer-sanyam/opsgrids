/**
 * A code as the server wants it: a TOTP code is read off a phone as `123 456`, and a recovery code
 * is pasted from wherever it was saved, so the whitespace goes and nothing else does. A recovery
 * code keeps whatever punctuation it was issued with — this does not guess what that is.
 */
export function withoutSpaces(value: string): string {
  return value.replace(/\s+/g, "");
}
