const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Whether `value` is shaped like a UUID — what a typed-in document, lot or roll id must be. */
export function isUuid(value: string): boolean {
  return UUID.test(value.trim());
}
