/**
 * The scan payload (inventory Phase 9, ADR-0027): what a label's QR code and Code 128 bar code
 * carry, and how a scanner's output is read back.
 *
 * `og:<type>:<code>` — `type` is `item`, `lot`, `roll` or `location`; `code` is the printed code
 * (the entity's `barcode` when it has one, otherwise its SKU / lot number / roll number; a
 * location's barcode). A scan with no `og:` prefix is a *bare code*, looked up across every type.
 *
 * This is the twin of `services/operations/app/inventory/barcode.py`; both are tested against
 * `libs/contracts/scan-payload-vectors.json`, so neither can drift.
 */

export type ScanType = "item" | "lot" | "roll" | "location";

export const SCAN_TYPES: readonly ScanType[] = ["item", "lot", "roll", "location"];

const PREFIX = "og";
/** Longest code any entity can carry (`barcode` is 64 characters server-side). */
export const MAX_CODE_LENGTH = 64;

export class ScanPayloadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ScanPayloadError";
  }
}

export interface ParsedScan {
  /** `null` for a bare code — the server then tries every type. */
  type: ScanType | null;
  code: string;
}

/** Code 128 subset B carries printable ASCII (32-126) — the only characters a label may use. */
export function code128Encodable(code: string): boolean {
  if (code.length === 0) return false;
  for (let i = 0; i < code.length; i += 1) {
    const point = code.charCodeAt(i);
    if (point < 32 || point > 126) return false;
  }
  return true;
}

function checkCode(code: string): void {
  if (code.length === 0) throw new ScanPayloadError("The label has no code");
  if (code.length > MAX_CODE_LENGTH) {
    throw new ScanPayloadError(`A code is at most ${MAX_CODE_LENGTH} characters`);
  }
  if (!code128Encodable(code)) {
    throw new ScanPayloadError("A code may only contain printable ASCII characters");
  }
}

function isScanType(value: string): value is ScanType {
  return (SCAN_TYPES as readonly string[]).includes(value);
}

/** The text a label encodes. Refuses a code that could not be printed or read back. */
export function formatPayload(type: ScanType, code: string): string {
  if (!isScanType(type)) throw new ScanPayloadError(`Unknown label type ${String(type)}`);
  checkCode(code);
  return `${PREFIX}:${type}:${code}`;
}

/**
 * Reads what a scanner (or a person typing) produced. Surrounding whitespace — scanners append a
 * newline or a tab — is dropped; nothing else is rewritten.
 */
export function parsePayload(text: string): ParsedScan {
  const value = text.trim();
  if (value === "") throw new ScanPayloadError("Nothing was scanned");
  if (value.startsWith(`${PREFIX}:`)) {
    const first = value.indexOf(":");
    const second = value.indexOf(":", first + 1);
    if (second === -1) throw new ScanPayloadError(`${value} is not a valid OpsGrids label`);
    const type = value.slice(first + 1, second);
    const code = value.slice(second + 1);
    if (!isScanType(type)) {
      throw new ScanPayloadError(`${value} names an unknown label type ${type}`);
    }
    checkCode(code);
    return { type, code };
  }
  checkCode(value);
  return { type: null, code: value };
}
