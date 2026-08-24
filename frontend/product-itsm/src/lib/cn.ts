/** Join class names, dropping falsey values. Small on purpose — no runtime dep. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
