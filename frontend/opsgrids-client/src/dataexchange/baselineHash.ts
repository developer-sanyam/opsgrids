/**
 * The hashing behind the export baseline (`baseline.ts`). The baseline never holds a cell's value,
 * only a salted digest of it: the import needs nothing but "is this cell what the export wrote?",
 * which equality of digests answers, and a file whose user deleted a sensitive column or its rows
 * must not still carry them in a hidden sheet.
 *
 * A digest is SHA-256 (WebCrypto, so no dependency and no main-thread hashing) of
 * `["<file salt>", "<column key>", "<record id>", "<cell token>"]` as JSON, cut to 80 bits and
 * written as unpadded base64 (14 characters). The salt is random per file; the column key and the
 * record id are inside the hashed text, so the same value in another column or another record has a
 * different digest, and a digest from one file says nothing about another. JSON is what keeps the
 * four parts apart (no separator a value could imitate) and tells a blank cell (`null`) from the
 * text "null".
 *
 * Cost. An export hashes every editable cell of every record: 10 000 records of 20 columns is 200 000
 * digests. They run in groups, and after each 8 ms of work the loop yields to the event loop, so the
 * page stays responsive whatever the size.
 */

/**
 * Bytes of the SHA-256 digest that are kept: 80 bits. All the import asks is whether a cell is what
 * the export wrote, so what is needed is that an edited cell never digests to its old value by
 * chance (one in 2^80) and cannot be made to by searching (2^80 work, with the salt in hand). More
 * bits would only grow the file: a baseline holds a digest for EVERY editable cell of EVERY exported
 * record, and a file over 5 MB is refused, so the length is what decides how many records and columns
 * fit under it.
 */
const DIGEST_BYTES = 10;
/** Characters of a digest as text: 10 bytes in unpadded base64. */
export const DIGEST_LENGTH = 14;
export const DIGEST_PATTERN = /^[A-Za-z0-9+/]{14}$/;
/** Bytes of the per-file salt: 128 bits, as long as a salt needs to be. */
const SALT_BYTES = 16;
/** A salt as text: 16 bytes in unpadded base64. */
export const SALT_PATTERN = /^[A-Za-z0-9+/]{22}$/;
/** Digests started together before the clock is looked at. */
const GROUP = 128;
/** How long hashing may run before it yields to the event loop. */
export const DEFAULT_SLICE_MS = 8;

const encoder = new TextEncoder();

function subtleCrypto(): SubtleCrypto | undefined {
  // `crypto.subtle` only exists in a secure context (HTTPS or localhost), and a very old browser has none.
  return (globalThis.crypto as Crypto | undefined)?.subtle;
}

/** Whether this browser context can hash. Without it an export carries no baseline and a baseline is not read. */
export function hashingAvailable(): boolean {
  return subtleCrypto() !== undefined;
}

function base64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/=+$/, "");
}

/** A new random salt, for one export. */
export function newSalt(): string {
  const bytes = new Uint8Array(SALT_BYTES);
  globalThis.crypto.getRandomValues(bytes);
  return base64(bytes);
}

/** What one cell's digest is made from. `token` is the cell as `cellToken` reads it: null is blank. */
export interface CellHashInput {
  key: string;
  recordId: string;
  token: string | null;
}

async function digestOf(subtle: SubtleCrypto, salt: string, input: CellHashInput): Promise<string> {
  const message = encoder.encode(JSON.stringify([salt, input.key, input.recordId, input.token]));
  const hash = await subtle.digest("SHA-256", message);
  return base64(new Uint8Array(hash, 0, DIGEST_BYTES));
}

function yieldToEventLoop(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

/**
 * The digest of every input, in order. Runs in groups and yields to the event loop after each
 * `sliceMs` of work, so a very large export never freezes the page. Throws when the context cannot
 * hash: callers check `hashingAvailable()` first.
 */
export async function digestAll(
  salt: string,
  inputs: readonly CellHashInput[],
  sliceMs: number = DEFAULT_SLICE_MS,
): Promise<string[]> {
  const subtle = subtleCrypto();
  if (subtle === undefined) throw new Error("This browser context cannot hash");
  const digests: string[] = [];
  let sliceStart = performance.now();
  for (let at = 0; at < inputs.length; at += GROUP) {
    const group = inputs.slice(at, at + GROUP);
    digests.push(...(await Promise.all(group.map((input) => digestOf(subtle, salt, input)))));
    // Nothing to yield for after the last group: the caller is next in line anyway.
    if (at + GROUP < inputs.length && performance.now() - sliceStart >= sliceMs) {
      await yieldToEventLoop();
      sliceStart = performance.now();
    }
  }
  return digests;
}
