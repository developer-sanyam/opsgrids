/**
 * A QR Code encoder, in the design system because a scannable code is a rendering concern and
 * no product should grow its own (ADR-0001).
 *
 * Byte mode only, which is the mode every payload this platform prints actually needs — a GST
 * signed-QR is base64url, a payment link is a URI, and both carry lowercase, so alphanumeric mode
 * is never available to them anyway. Numeric and kanji modes are therefore absent rather than
 * stubbed: an encoder that claims a mode it does not implement is worse than one that does not.
 *
 * The output is a matrix of modules, not an image. `QrCode` draws it as an SVG; anything else that
 * needs one (a canvas, a print sheet) draws the same matrix its own way.
 *
 * Correctness here is not a matter of taste — a wrong error-correction block layout produces a
 * code that renders beautifully and scans as nothing. The block tables and the polynomial
 * arithmetic follow ISO/IEC 18004, and `qr.test.ts` checks the module matrix against a reference
 * encoder for every mask on a real payload.
 */

/** Error-correction level: how much of the code can be lost and still read. */
export type QrLevel = "L" | "M" | "Q" | "H";

/** An encoded code: a square grid of dark/light modules, plus how it was encoded. */
export interface QrMatrix {
  /** Modules per side, `4 × version + 17`. */
  readonly size: number;
  /** Row-major; `modules[y][x]` is true where the module is dark. */
  readonly modules: readonly (readonly boolean[])[];
  readonly version: number;
  readonly level: QrLevel;
  readonly mask: number;
}

export interface EncodeOptions {
  /** Default `"M"` — the level that survives an ordinary print or a phone camera at an angle. */
  level?: QrLevel;
  /** Force a mask (0–7) instead of choosing by penalty score. Tests use it; nothing else should. */
  mask?: number;
}

const MIN_VERSION = 1;
const MAX_VERSION = 40;

/** The 2-bit-per-level code the format information carries. Not the same order as `QrLevel`. */
const FORMAT_BITS: Record<QrLevel, number> = { L: 1, M: 0, Q: 3, H: 2 };

// Index 0 is unused so the arrays are indexed by version directly. ISO/IEC 18004 tables 13–22.
const ECC_CODEWORDS_PER_BLOCK: Record<QrLevel, readonly number[]> = {
  L: [
    -1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30,
    30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
  ],
  M: [
    -1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28,
    28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
  ],
  Q: [
    -1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30,
    30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
  ],
  H: [
    -1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30,
    30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
  ],
};

const NUM_ERROR_CORRECTION_BLOCKS: Record<QrLevel, readonly number[]> = {
  L: [
    -1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14,
    15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25,
  ],
  M: [
    -1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23,
    25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49,
  ],
  Q: [
    -1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34,
    34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68,
  ],
  H: [
    -1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35,
    37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81,
  ],
};

/* --------------------------------------------------------------- small typed-array reads
 * `noUncheckedIndexedAccess` is on, and rightly: an out-of-range read is a real bug class. These
 * two say "out of range reads as zero", which is exactly the arithmetic the polynomial code wants
 * at a boundary — rather than a `!` that would silence the compiler and the bug together. */

function byteAt(bytes: Uint8Array, i: number): number {
  return bytes[i] ?? 0;
}

function numAt(values: readonly number[], i: number): number {
  return values[i] ?? 0;
}

/** Bit `i` of `x`, counting from the least significant. */
function getBit(x: number, i: number): boolean {
  return ((x >>> i) & 1) !== 0;
}

/* ------------------------------------------------------------------------ capacity maths */

/**
 * Every module a version has available for data and error correction, before the format and
 * version information take their share. Derived rather than tabulated: the geometry is the
 * definition, and a table of it is one more thing to get wrong.
 */
function numRawDataModules(version: number): number {
  let result = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const numAlign = Math.floor(version / 7) + 2;
    result -= (25 * numAlign - 10) * numAlign - 55;
    if (version >= 7) result -= 36;
  }
  return result;
}

/** How many 8-bit data codewords one version holds at one level, error correction removed. */
function numDataCodewords(version: number, level: QrLevel): number {
  return (
    Math.floor(numRawDataModules(version) / 8) -
    numAt(ECC_CODEWORDS_PER_BLOCK[level], version) *
      numAt(NUM_ERROR_CORRECTION_BLOCKS[level], version)
  );
}

/** Byte mode states its length in 8 bits up to version 9 and 16 bits from version 10. */
function charCountBits(version: number): number {
  return version <= 9 ? 8 : 16;
}

/* --------------------------------------------------------------------- Reed–Solomon (GF 256) */

/** Multiply in GF(2^8) modulo the QR primitive polynomial `x^8 + x^4 + x^3 + x^2 + 1`. */
function gfMultiply(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}

/** The generator polynomial of the given degree, coefficients descending, leading 1 implicit. */
function rsDivisor(degree: number): Uint8Array {
  const result = new Uint8Array(degree);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      result[j] = gfMultiply(byteAt(result, j), root) ^ byteAt(result, j + 1);
    }
    root = gfMultiply(root, 0x02);
  }
  return result;
}

/** The error-correction codewords for one block: the remainder of the data over the divisor. */
function rsRemainder(data: Uint8Array, divisor: Uint8Array): Uint8Array {
  const result = new Uint8Array(divisor.length);
  for (const b of data) {
    const factor = b ^ byteAt(result, 0);
    result.copyWithin(0, 1);
    result[result.length - 1] = 0;
    for (let i = 0; i < result.length; i++) {
      result[i] = byteAt(result, i) ^ gfMultiply(byteAt(divisor, i), factor);
    }
  }
  return result;
}

/**
 * Split the data into blocks, append each block's error correction, and interleave the lot.
 *
 * Interleaving is what makes error correction work against a smudge: a physical blot destroys
 * consecutive modules, and consecutive modules belong to different blocks, so no one block loses
 * more than it can repair.
 */
function addEccAndInterleave(data: Uint8Array, version: number, level: QrLevel): Uint8Array {
  const numBlocks = numAt(NUM_ERROR_CORRECTION_BLOCKS[level], version);
  const blockEccLen = numAt(ECC_CODEWORDS_PER_BLOCK[level], version);
  const rawCodewords = Math.floor(numRawDataModules(version) / 8);
  const numShortBlocks = numBlocks - (rawCodewords % numBlocks);
  const shortBlockLen = Math.floor(rawCodewords / numBlocks);

  const divisor = rsDivisor(blockEccLen);
  const blocks: number[][] = [];
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const datLen = shortBlockLen - blockEccLen + (i < numShortBlocks ? 0 : 1);
    const dat = Array.from(data.slice(k, k + datLen));
    k += datLen;
    const ecc = Array.from(rsRemainder(Uint8Array.from(dat), divisor));
    // A short block carries a hole where the long blocks carry their last data codeword; the
    // interleave below skips it, so the padding never reaches the code.
    if (i < numShortBlocks) dat.push(0);
    blocks.push(dat.concat(ecc));
  }

  const result: number[] = [];
  const blockLen = shortBlockLen + 1;
  for (let i = 0; i < blockLen; i++) {
    for (let j = 0; j < blocks.length; j++) {
      if (i !== shortBlockLen - blockEccLen || j >= numShortBlocks) {
        result.push(numAt(blocks[j] ?? [], i));
      }
    }
  }
  return Uint8Array.from(result);
}

/* ------------------------------------------------------------------------- the bit stream */

/** Mode indicator, length, payload, terminator and the alternating pad bytes — in that order. */
function buildCodewords(bytes: Uint8Array, version: number, level: QrLevel): Uint8Array {
  const bits: boolean[] = [];
  const append = (value: number, width: number) => {
    for (let i = width - 1; i >= 0; i--) bits.push(getBit(value, i));
  };

  append(0b0100, 4); // byte mode
  append(bytes.length, charCountBits(version));
  for (const b of bytes) append(b, 8);

  const capacityBits = numDataCodewords(version, level) * 8;
  // The terminator is up to four zero bits — fewer when the capacity runs out first.
  for (let i = 0; i < 4 && bits.length < capacityBits; i++) bits.push(false);
  while (bits.length % 8 !== 0) bits.push(false);

  const codewords = new Uint8Array(capacityBits / 8);
  for (let i = 0; i < bits.length; i++) {
    if (bits[i] === true) {
      const index = i >>> 3;
      codewords[index] = byteAt(codewords, index) | (0x80 >>> (i & 7));
    }
  }
  // 0xEC / 0x11 alternating: the standard's own filler, chosen because the pair is visually
  // busy and therefore does not bias the mask penalty toward a large blank region.
  for (let i = bits.length / 8, pad = 0xec; i < codewords.length; i++, pad ^= 0xec ^ 0x11) {
    codewords[i] = pad;
  }
  return codewords;
}

/* --------------------------------------------------------------------------- the matrix */

interface Grid {
  size: number;
  modules: boolean[][];
  isFunction: boolean[][];
}

function newGrid(size: number): Grid {
  const blank = (): boolean[][] =>
    Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  return { size, modules: blank(), isFunction: blank() };
}

function setModule(grid: Grid, x: number, y: number, dark: boolean): void {
  const row = grid.modules[y];
  if (row !== undefined && x >= 0 && x < grid.size) row[x] = dark;
}

function moduleAt(grid: Grid, x: number, y: number): boolean {
  return grid.modules[y]?.[x] ?? false;
}

function isFunctionAt(grid: Grid, x: number, y: number): boolean {
  return grid.isFunction[y]?.[x] ?? false;
}

/** Set a module and mark it as function patterning, so the data placement steps over it. */
function setFunctionModule(grid: Grid, x: number, y: number, dark: boolean): void {
  if (x < 0 || x >= grid.size || y < 0 || y >= grid.size) return;
  setModule(grid, x, y, dark);
  const row = grid.isFunction[y];
  if (row !== undefined) row[x] = true;
}

/** The three big squares a scanner locks onto, drawn from their centre outward. */
function drawFinder(grid: Grid, x: number, y: number): void {
  for (let dy = -4; dy <= 4; dy++) {
    for (let dx = -4; dx <= 4; dx++) {
      const dist = Math.max(Math.abs(dx), Math.abs(dy));
      setFunctionModule(grid, x + dx, y + dy, dist !== 2 && dist !== 4);
    }
  }
}

/** The small squares that keep a large code from drifting out of alignment as it is read. */
function drawAlignment(grid: Grid, x: number, y: number): void {
  for (let dy = -2; dy <= 2; dy++) {
    for (let dx = -2; dx <= 2; dx++) {
      setFunctionModule(grid, x + dx, y + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }
}

/** Where the alignment patterns sit for a version. Empty at version 1, which has none. */
function alignmentPositions(version: number): number[] {
  if (version === 1) return [];
  const numAlign = Math.floor(version / 7) + 2;
  const step = version === 32 ? 26 : Math.ceil((version * 4 + 4) / (numAlign * 2 - 2)) * 2;
  const size = version * 4 + 17;
  const result: number[] = [6];
  for (let pos = size - 7; result.length < numAlign; pos -= step) result.splice(1, 0, pos);
  return result;
}

/** Level and mask, BCH(15,5)-encoded and masked, written twice so either copy can be lost. */
function drawFormatBits(grid: Grid, level: QrLevel, mask: number): void {
  const data = (FORMAT_BITS[level] << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  const bits = ((data << 10) | rem) ^ 0x5412;
  const size = grid.size;

  for (let i = 0; i <= 5; i++) setFunctionModule(grid, 8, i, getBit(bits, i));
  setFunctionModule(grid, 8, 7, getBit(bits, 6));
  setFunctionModule(grid, 8, 8, getBit(bits, 7));
  setFunctionModule(grid, 7, 8, getBit(bits, 8));
  for (let i = 9; i < 15; i++) setFunctionModule(grid, 14 - i, 8, getBit(bits, i));

  for (let i = 0; i < 8; i++) setFunctionModule(grid, size - 1 - i, 8, getBit(bits, i));
  for (let i = 8; i < 15; i++) setFunctionModule(grid, 8, size - 15 + i, getBit(bits, i));
  setFunctionModule(grid, 8, size - 8, true); // the one module that is always dark
}

/** Version 7 and up state their version in two BCH(18,6) blocks near the far corners. */
function drawVersionBits(grid: Grid, version: number): void {
  if (version < 7) return;
  let rem = version;
  for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
  const bits = (version << 12) | rem;
  for (let i = 0; i < 18; i++) {
    const dark = getBit(bits, i);
    const a = grid.size - 11 + (i % 3);
    const b = Math.floor(i / 3);
    setFunctionModule(grid, a, b, dark);
    setFunctionModule(grid, b, a, dark);
  }
}

function drawFunctionPatterns(grid: Grid, version: number, level: QrLevel): void {
  const size = grid.size;
  for (let i = 0; i < size; i++) {
    setFunctionModule(grid, 6, i, i % 2 === 0);
    setFunctionModule(grid, i, 6, i % 2 === 0);
  }

  drawFinder(grid, 3, 3);
  drawFinder(grid, size - 4, 3);
  drawFinder(grid, 3, size - 4);

  const positions = alignmentPositions(version);
  const last = positions.length - 1;
  for (let i = 0; i <= last; i++) {
    for (let j = 0; j <= last; j++) {
      // The three corners belong to the finders; an alignment pattern there would overwrite them.
      const corner = (i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0);
      if (!corner) drawAlignment(grid, numAt(positions, i), numAt(positions, j));
    }
  }

  drawFormatBits(grid, level, 0); // rewritten once the mask is chosen
  drawVersionBits(grid, version);
}

/** Lay the interleaved codewords down the two-module-wide zigzag, right to left. */
function drawCodewords(grid: Grid, data: Uint8Array): void {
  const size = grid.size;
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    // Column 6 is the vertical timing pattern. The pair shifts left onto column 5 and *stays*
    // shifted for the rest of the walk — every pair after it is offset by one, which is why this
    // reassigns the loop variable rather than using a local copy.
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - vert : vert;
        if (!isFunctionAt(grid, x, y) && i < data.length * 8) {
          setModule(grid, x, y, getBit(byteAt(data, i >>> 3), 7 - (i & 7)));
          i++;
        }
      }
    }
  }
}

/** The eight mask patterns, by index. Each inverts the data modules it selects. */
function maskSelects(mask: number, x: number, y: number): boolean {
  switch (mask) {
    case 0:
      return (x + y) % 2 === 0;
    case 1:
      return y % 2 === 0;
    case 2:
      return x % 3 === 0;
    case 3:
      return (x + y) % 3 === 0;
    case 4:
      return (Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0;
    case 5:
      return ((x * y) % 2) + ((x * y) % 3) === 0;
    case 6:
      return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
    case 7:
      return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
    default:
      throw new RangeError(`QR mask must be 0–7, got ${mask}`);
  }
}

function applyMask(grid: Grid, mask: number): void {
  for (let y = 0; y < grid.size; y++) {
    for (let x = 0; x < grid.size; x++) {
      if (!isFunctionAt(grid, x, y) && maskSelects(mask, x, y)) {
        setModule(grid, x, y, !moduleAt(grid, x, y));
      }
    }
  }
}

// The four penalty weights from the standard. A mask is chosen by minimising their sum, which is
// what stops a code from developing large blank areas or shapes a scanner mistakes for a finder.
const PENALTY_RUN = 3;
const PENALTY_BLOCK = 3;
const PENALTY_FINDER_LOOKALIKE = 40;
const PENALTY_IMBALANCE = 10;

/** The 1:1:3:1:1 finder signature with its quiet zone, in both orientations. */
const FINDER_LOOKALIKES: readonly boolean[][] = [
  [true, false, true, true, true, false, true, false, false, false, false],
  [false, false, false, false, true, false, true, true, true, false, true],
];

function matchesAt(line: readonly boolean[], at: number, pattern: readonly boolean[]): boolean {
  for (let i = 0; i < pattern.length; i++) {
    if ((line[at + i] ?? false) !== (pattern[i] ?? false)) return false;
  }
  return true;
}

function linePenalty(line: readonly boolean[]): number {
  let penalty = 0;
  let run = 1;
  for (let i = 1; i < line.length; i++) {
    if (line[i] === line[i - 1]) {
      run++;
      if (run === 5) penalty += PENALTY_RUN;
      else if (run > 5) penalty += 1;
    } else {
      run = 1;
    }
  }
  for (let i = 0; i + 11 <= line.length; i++) {
    for (const pattern of FINDER_LOOKALIKES) {
      if (matchesAt(line, i, pattern)) penalty += PENALTY_FINDER_LOOKALIKE;
    }
  }
  return penalty;
}

function penaltyScore(grid: Grid): number {
  const size = grid.size;
  let penalty = 0;
  let dark = 0;

  for (let y = 0; y < size; y++) {
    const row: boolean[] = [];
    const col: boolean[] = [];
    for (let x = 0; x < size; x++) {
      row.push(moduleAt(grid, x, y));
      col.push(moduleAt(grid, y, x));
      if (moduleAt(grid, x, y)) dark++;
    }
    penalty += linePenalty(row) + linePenalty(col);
  }

  for (let y = 0; y < size - 1; y++) {
    for (let x = 0; x < size - 1; x++) {
      const first = moduleAt(grid, x, y);
      if (
        first === moduleAt(grid, x + 1, y) &&
        first === moduleAt(grid, x, y + 1) &&
        first === moduleAt(grid, x + 1, y + 1)
      ) {
        penalty += PENALTY_BLOCK;
      }
    }
  }

  const total = size * size;
  // Every full 5% the dark proportion strays from half costs one step.
  const deviation = Math.floor((Math.abs(dark * 20 - total * 10) + total - 1) / total);
  return penalty + deviation * PENALTY_IMBALANCE;
}

/* ------------------------------------------------------------------------------ the encoder */

/**
 * Encode `text` as a QR Code, choosing the smallest version that holds it at `level`.
 *
 * Throws when the text does not fit version 40 — the honest answer, since a truncated payload
 * would produce a code that scans cleanly into the wrong data.
 */
export function encodeQr(text: string, options: EncodeOptions = {}): QrMatrix {
  const { level = "M" } = options;
  const bytes = new TextEncoder().encode(text);

  let version = MIN_VERSION;
  for (; version <= MAX_VERSION; version++) {
    const capacity = numDataCodewords(version, level) * 8;
    if (4 + charCountBits(version) + bytes.length * 8 <= capacity) break;
  }
  if (version > MAX_VERSION) {
    throw new RangeError(
      `${bytes.length} bytes exceed what a QR Code holds at level ${level} (version 40 maximum)`,
    );
  }

  const codewords = addEccAndInterleave(buildCodewords(bytes, version, level), version, level);
  const grid = newGrid(version * 4 + 17);
  drawFunctionPatterns(grid, version, level);
  drawCodewords(grid, codewords);

  let chosen = options.mask;
  if (chosen === undefined) {
    let best = Infinity;
    for (let mask = 0; mask < 8; mask++) {
      applyMask(grid, mask);
      drawFormatBits(grid, level, mask);
      const score = penaltyScore(grid);
      if (score < best) {
        best = score;
        chosen = mask;
      }
      applyMask(grid, mask); // the mask is its own inverse
    }
  }
  const mask = chosen ?? 0;
  applyMask(grid, mask);
  drawFormatBits(grid, level, mask);

  return { size: grid.size, modules: grid.modules, version, level, mask };
}

/**
 * The matrix as one SVG path definition, in module units, with `quietZone` modules of margin.
 *
 * One path rather than a rect per module: a version-20 code is 9,409 modules, and 9,409 DOM nodes
 * is a scroll jank nobody needs to pay for a picture of a string.
 */
export function qrPathData(matrix: QrMatrix, quietZone = 4): string {
  const parts: string[] = [];
  for (let y = 0; y < matrix.size; y++) {
    for (let x = 0; x < matrix.size; x++) {
      if (matrix.modules[y]?.[x] === true) {
        parts.push(`M${x + quietZone} ${y + quietZone}h1v1h-1z`);
      }
    }
  }
  return parts.join("");
}
