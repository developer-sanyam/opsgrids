/**
 * Postal codes: normalising them, reading them out of what a merchant pasted or uploaded, and
 * working out what a save would actually do **before** anything is written.
 *
 * Everything here is pure. That is deliberate: this is where the bugs are. A merchant onboarding a
 * city pastes two hundred codes from a spreadsheet, and the difference between a good day and a
 * support ticket is whether the screen said "eleven of these are in Andheri West today, moving
 * them here" or silently moved them.
 *
 * **One postal code belongs to exactly one zone per organization** (ADR-0010 §2, and the UNIQUE
 * index in §10). So re-assignment is not an edge case, it is the normal act of growing a zone —
 * and it is a decision the merchant makes with the list in front of them, never a last-writer-wins
 * the server performs quietly.
 *
 * The plan this file produces is a **preview**, not an authority. The server recomputes it inside
 * one transaction and returns its own counters, which is what the screen shows afterwards.
 */

import type { ImportMode } from "./types";

/** ADR-0010 §2: `String(16)`, wide enough for a UK or Canadian code so a second country is a
 *  validator change rather than a migration. */
export const POSTAL_CODE_MAX_LENGTH = 16;

/** Why a token could not be taken. The copy for each lives in `rejectionMessage`. */
export type RejectReason = "not-indian-pin" | "too-long";

export interface RejectedEntry {
  /** What the merchant actually typed, so they can find it in their spreadsheet. */
  raw: string;
  /** The normalised form, which is what the rule was applied to. */
  value: string;
  /** 1-based, counted in the pasted text or the uploaded file. */
  line: number;
  reason: RejectReason;
}

export interface ParsedCode {
  value: string;
  raw: string;
  line: number;
}

export interface ParsedList {
  codes: ParsedCode[];
  rejected: RejectedEntry[];
}

/**
 * Upper-case, non-alphanumerics stripped — the stored form (ADR-0010 §2).
 *
 * `" 400 050 "`, `"400-050"` and `"400050"` are the same postal code, and a merchant who pastes a
 * column out of Excel gets all three. Normalising here rather than at the comparison is what makes
 * "already present" honest.
 */
export function normalisePostalCode(raw: string): string {
  return raw.replace(/[^0-9a-zA-Z]/g, "").toUpperCase();
}

/**
 * India's six-digit form, the one country slice 1 validates (ADR-0010 §2).
 *
 * The leading digit is 1–9: no Indian PIN begins with 0, and `045123` in a pasted column is almost
 * always a spreadsheet that ate a leading apostrophe rather than a real code.
 */
export function isValidPostalCode(value: string): boolean {
  return /^[1-9][0-9]{5}$/.test(value);
}

/** The sentence shown beside a rejected code. Says the rule, not just that it failed. */
export function rejectionMessage(reason: RejectReason): string {
  if (reason === "too-long") {
    return `Longer than ${POSTAL_CODE_MAX_LENGTH} characters`;
  }
  return "Not a six-digit Indian PIN code";
}

function classify(raw: string, value: string, line: number): RejectedEntry | null {
  if (value.length > POSTAL_CODE_MAX_LENGTH) return { raw, value, line, reason: "too-long" };
  if (!isValidPostalCode(value)) return { raw, value, line, reason: "not-indian-pin" };
  return null;
}

/**
 * Split one line into candidate codes.
 *
 * **A single space is genuinely ambiguous** and this is the one place that has to decide: `400 050`
 * is how an Indian PIN is written on a signboard, and `400050 400051` is how two of them get
 * pasted. Splitting on every space breaks the first; splitting on none breaks the second.
 *
 * So the whole token is tried first — if it normalises to a valid code, the space was *inside* one
 * — and only if that fails is it split on whitespace and each piece tried. `400 050 400 051`
 * satisfies neither reading and comes back as four rejections with a line number, which is the
 * honest answer to an input nobody can read unambiguously.
 */
function tokenise(rawLine: string): string[] {
  const out: string[] = [];
  for (const chunk of rawLine.split(/[,;\t]|\s{2,}/)) {
    const raw = chunk.trim();
    if (raw === "") continue;
    if (isValidPostalCode(normalisePostalCode(raw)) || !/\s/.test(raw)) {
      out.push(raw);
      continue;
    }
    for (const piece of raw.split(/\s+/)) {
      if (piece !== "") out.push(piece);
    }
  }
  return out;
}

/**
 * Read a pasted block: one code per line, or comma-separated, or both, or tab-separated because
 * that is what a spreadsheet puts on the clipboard.
 *
 * Line numbers survive so a rejection can point at a row. Blank lines and blank cells are not
 * rejections — they are how people type lists — and are dropped silently.
 */
export function parsePostalCodeText(text: string): ParsedList {
  const codes: ParsedCode[] = [];
  const rejected: RejectedEntry[] = [];

  text.split(/\r\n|\r|\n/).forEach((rawLine, index) => {
    const line = index + 1;
    for (const raw of tokenise(rawLine)) {
      const value = normalisePostalCode(raw);
      if (value === "") continue;
      const problem = classify(raw, value, line);
      if (problem) rejected.push(problem);
      else codes.push({ value, raw, line });
    }
  });

  return { codes, rejected };
}

/* ------------------------------------- CSV -------------------------------------- */

/**
 * One row of a CSV, split into cells, with the line it came from.
 *
 * A hand-rolled reader rather than a dependency: the whole grammar in play is quotes, doubled
 * quotes and CRLF, and pulling a parser in for two columns would be a bundle cost with a licence
 * attached. It handles a quoted cell containing a comma or a newline, because a zone called
 * `Andheri East, Mumbai` is a thing a merchant will write.
 */
export interface CsvRow {
  cells: string[];
  line: number;
}

export function parseCsvRows(text: string): CsvRow[] {
  const rows: CsvRow[] = [];
  let cells: string[] = [];
  let cell = "";
  let quoted = false;
  let line = 1;
  let rowLine = 1;
  let touched = false;

  function endCell() {
    cells.push(cell);
    cell = "";
  }

  function endRow() {
    endCell();
    if (touched) rows.push({ cells, line: rowLine });
    cells = [];
    touched = false;
    rowLine = line;
  }

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === undefined) break;

    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        if (char === "\n") line += 1;
        cell += char;
      }
      continue;
    }

    if (char === '"') {
      quoted = true;
      touched = true;
      continue;
    }
    if (char === ",") {
      endCell();
      touched = true;
      continue;
    }
    if (char === "\r") continue;
    if (char === "\n") {
      line += 1;
      endRow();
      continue;
    }
    if (char.trim() !== "") touched = true;
    cell += char;
  }

  endRow();
  return rows;
}

const POSTAL_HEADERS = ["postal_code", "postalcode", "postcode", "pincode", "pin", "zip"];
const ZONE_HEADERS = ["zone_code", "zonecode", "zone", "zone_id"];

function headerKey(cell: string): string {
  return cell.trim().toLowerCase().replace(/\s+/g, "_");
}

/**
 * Which cell holds the postal code and which holds the zone, decided once for the file.
 *
 * A header row is detected by name, not by position, so `zone_code,postal_code` works. With no
 * header the first two non-empty cells are taken, in the order ADR-0010 §9 specifies
 * (`postal_code,zone_code`).
 */
interface CsvShape {
  postalIndex: number | null;
  zoneIndex: number | null;
  headerLine: number | null;
}

function detectShape(first: CsvRow | undefined): CsvShape {
  if (!first) return { postalIndex: null, zoneIndex: null, headerLine: null };
  const keys = first.cells.map(headerKey);
  const postalIndex = keys.findIndex((key) => POSTAL_HEADERS.includes(key));
  const zoneIndex = keys.findIndex((key) => ZONE_HEADERS.includes(key));
  if (postalIndex !== -1 && zoneIndex !== -1) {
    return { postalIndex, zoneIndex, headerLine: first.line };
  }
  return { postalIndex: null, zoneIndex: null, headerLine: null };
}

/** A data row a merchant can act on: two values and the line they were written on. */
export interface ImportRow {
  postalRaw: string;
  postalCode: string;
  zoneCode: string;
  line: number;
}

/** A row that carried neither two usable values nor a reason to be skipped. */
export interface MalformedRow {
  line: number;
  detail: string;
}

export interface ParsedImportCsv {
  rows: ImportRow[];
  malformed: MalformedRow[];
  rejected: RejectedEntry[];
  /** The line a header was found on, or `null` when the file goes straight into data. */
  headerLine: number | null;
}

/**
 * Read the two-column import file (ADR-0010 §9).
 *
 * Tolerant in the three ways a real merchant's export is untidy, and strict everywhere else:
 * a header row is recognised and skipped, empty cells are ignored so a trailing comma or a blank
 * middle column costs nothing, and quoted cells behave. A row that still has fewer than two
 * usable values after that is reported with its line number rather than dropped, because a row
 * silently dropped from a 19,000-line file is found by a customer.
 */
export function parseImportCsv(text: string): ParsedImportCsv {
  const all = parseCsvRows(text);
  const shape = detectShape(all[0]);
  const body = shape.headerLine === null ? all : all.slice(1);

  const rows: ImportRow[] = [];
  const malformed: MalformedRow[] = [];
  const rejected: RejectedEntry[] = [];

  for (const row of body) {
    let postalRaw: string;
    let zoneRaw: string;

    if (shape.postalIndex !== null && shape.zoneIndex !== null) {
      postalRaw = (row.cells[shape.postalIndex] ?? "").trim();
      zoneRaw = (row.cells[shape.zoneIndex] ?? "").trim();
    } else {
      const filled = row.cells.map((cell) => cell.trim()).filter((cell) => cell !== "");
      postalRaw = filled[0] ?? "";
      zoneRaw = filled[1] ?? "";
    }

    if (postalRaw === "" && zoneRaw === "") continue;
    if (postalRaw === "") {
      malformed.push({ line: row.line, detail: "No postal code in this row" });
      continue;
    }
    if (zoneRaw === "") {
      malformed.push({ line: row.line, detail: "No zone code in this row" });
      continue;
    }

    const postalCode = normalisePostalCode(postalRaw);
    const problem = classify(postalRaw, postalCode, row.line);
    if (problem) {
      rejected.push(problem);
      continue;
    }
    rows.push({ postalRaw, postalCode, zoneCode: zoneRaw.toUpperCase(), line: row.line });
  }

  return { rows, malformed, rejected, headerLine: shape.headerLine };
}

/* ------------------------------------ planning ----------------------------------- */

/** Who owns a postal code right now, as the org's current map says. */
export interface PostalCodeOwner {
  zoneId: string;
  zoneCode: string;
  zoneName: string;
}

/** The organization's whole map: postal code → the one zone that holds it. */
export type PostalCodeMap = ReadonlyMap<string, PostalCodeOwner>;

/** The same code written twice, and where. */
export interface DuplicateEntry {
  value: string;
  lines: number[];
}

/** A code that is about to change hands. */
export interface Move {
  value: string;
  from: PostalCodeOwner;
}

/**
 * What a save would do, counted honestly.
 *
 * Every field here answers a question a merchant actually asks, and `moves` is the one that earns
 * the whole screen: it names which zone each code is leaving.
 */
export interface SavePlan {
  /** Exactly what would be written: normalised, de-duplicated, first occurrence wins. */
  save: string[];
  /** Not in the organization's map at all today. */
  added: string[];
  /** In another zone today. The decision the merchant is being shown. */
  moves: Move[];
  /** Already in this zone. Saving changes nothing for them. */
  unchanged: string[];
  /** In this zone today and absent from what was pasted — so the save drops them. */
  removed: string[];
  /** Written more than once in the input. Collapsed, and said out loud. */
  duplicates: DuplicateEntry[];
  /** Refused before anything is sent, with the rule that refused them. */
  rejected: RejectedEntry[];
}

function ownedBy(map: PostalCodeMap, zoneId: string): string[] {
  const owned: string[] = [];
  for (const [code, owner] of map) if (owner.zoneId === zoneId) owned.push(code);
  return owned.sort();
}

function collectDuplicates(entries: readonly { value: string; line: number }[]): {
  unique: string[];
  duplicates: DuplicateEntry[];
} {
  const lines = new Map<string, number[]>();
  const order: string[] = [];
  for (const entry of entries) {
    const seen = lines.get(entry.value);
    if (seen) seen.push(entry.line);
    else {
      lines.set(entry.value, [entry.line]);
      order.push(entry.value);
    }
  }
  const duplicates: DuplicateEntry[] = [];
  for (const value of order) {
    const at = lines.get(value);
    if (at && at.length > 1) duplicates.push({ value, lines: at });
  }
  return { unique: order, duplicates };
}

/**
 * Plan one zone's postal-code save — the textarea path
 * (`PUT /delivery/zones/{id}/postal-codes`, ADR-0010 §9).
 *
 * The PUT carries the whole set, so **what is missing from the textarea is removed from the zone**.
 * That is the semantic the merchant needs told to them in numbers before they press save, and it
 * is why `removed` is a first-class count here rather than a surprise in the audit log.
 *
 * A duplicate inside one paste is collapsed rather than refused: a person typing a list repeats
 * themselves, and ADR-0010 §2 says the textarea is "de-duplicated and validated on save". The CSV
 * import is the one that refuses — see `planImport`.
 */
export function planZoneSave(text: string, zoneId: string, map: PostalCodeMap): SavePlan {
  const parsed = parsePostalCodeText(text);
  const { unique, duplicates } = collectDuplicates(parsed.codes);

  const added: string[] = [];
  const moves: Move[] = [];
  const unchanged: string[] = [];

  for (const value of unique) {
    const owner = map.get(value);
    if (!owner) added.push(value);
    else if (owner.zoneId === zoneId) unchanged.push(value);
    else moves.push({ value, from: owner });
  }

  const keeping = new Set(unique);
  const removed = ownedBy(map, zoneId).filter((code) => !keeping.has(code));

  return { save: unique, added, moves, unchanged, removed, duplicates, rejected: parsed.rejected };
}

/** A `zone_code` in the file that no zone in this organization answers to. */
export interface UnknownZone {
  code: string;
  lines: number[];
}

export interface ImportPlan extends SavePlan {
  mode: ImportMode;
  /** Data rows read, header excluded. The first of ADR-0010 §9's five counters. */
  rowsRead: number;
  /** Rows that were not two values. Reported by line, never dropped. */
  malformed: MalformedRow[];
  unknownZones: UnknownZone[];
  /** Per destination zone, so the merchant can read the file back as a map. */
  perZone: { zoneId: string; zoneCode: string; zoneName: string; count: number }[];
  /**
   * True when the file must be fixed before it can be sent. ADR-0010 §9 makes an import all or
   * nothing and refuses a duplicated postal code by line number; a zone code that names nothing
   * has no destination to write to. Both are stopped here as well as there — the client check
   * mirrors the server's rule, it does not replace it.
   */
  blocked: boolean;
}

/**
 * Plan a whole-file import (`POST /delivery/postal-codes/import`, ADR-0010 §9).
 *
 * `replace` deletes every mapping for the organization first, so the removals a merchant made in
 * their master file finally take effect — which is exactly why it is dangerous, and why this
 * returns `removed` for it. `merge` removes nothing.
 */
export function planImport(
  text: string,
  mode: ImportMode,
  zones: readonly { id: string; code: string; name: string }[],
  map: PostalCodeMap,
): ImportPlan {
  const parsed = parseImportCsv(text);
  const byCode = new Map(zones.map((zone) => [zone.code.toUpperCase(), zone]));

  const unknownLines = new Map<string, number[]>();
  const resolved: { value: string; line: number; zone: { id: string; code: string; name: string } }[] =
    [];

  for (const row of parsed.rows) {
    const zone = byCode.get(row.zoneCode);
    if (!zone) {
      const at = unknownLines.get(row.zoneCode);
      if (at) at.push(row.line);
      else unknownLines.set(row.zoneCode, [row.line]);
      continue;
    }
    resolved.push({ value: row.postalCode, line: row.line, zone });
  }

  const { duplicates } = collectDuplicates(
    parsed.rows.map((row) => ({ value: row.postalCode, line: row.line })),
  );

  const seen = new Set<string>();
  const save: string[] = [];
  const added: string[] = [];
  const moves: Move[] = [];
  const unchanged: string[] = [];
  const perZoneCount = new Map<string, { zoneId: string; zoneCode: string; zoneName: string; count: number }>();

  for (const entry of resolved) {
    if (seen.has(entry.value)) continue;
    seen.add(entry.value);
    save.push(entry.value);

    const bucket = perZoneCount.get(entry.zone.id);
    if (bucket) bucket.count += 1;
    else {
      perZoneCount.set(entry.zone.id, {
        zoneId: entry.zone.id,
        zoneCode: entry.zone.code,
        zoneName: entry.zone.name,
        count: 1,
      });
    }

    const owner = map.get(entry.value);
    if (!owner) added.push(entry.value);
    else if (owner.zoneId === entry.zone.id) unchanged.push(entry.value);
    else moves.push({ value: entry.value, from: owner });
  }

  const removed = mode === "replace" ? [...map.keys()].filter((code) => !seen.has(code)).sort() : [];

  const unknownZones: UnknownZone[] = [...unknownLines].map(([code, lines]) => ({ code, lines }));

  return {
    mode,
    rowsRead: parsed.rows.length + parsed.rejected.length + parsed.malformed.length,
    save,
    added,
    moves,
    unchanged,
    removed,
    duplicates,
    rejected: parsed.rejected,
    malformed: parsed.malformed,
    unknownZones,
    perZone: [...perZoneCount.values()],
    blocked: duplicates.length > 0 || unknownZones.length > 0,
  };
}

/** Whether a plan would change anything at all. A save that is a no-op says so instead of pretending. */
export function planIsNoop(plan: SavePlan): boolean {
  return plan.added.length === 0 && plan.moves.length === 0 && plan.removed.length === 0;
}
