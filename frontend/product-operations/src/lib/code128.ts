/**
 * A Code 128 (subset B) encoder — the linear bar code on every inventory label, beside the QR.
 *
 * Subset B carries printable ASCII, which is exactly what a label code may contain
 * (`code128Encodable`), so no subset switching is needed and the output is a pure function of the
 * text. The table is checked against an independent encoder (`python-barcode`) for every
 * printable character in `code128.test.ts`.
 */

/** Element widths (bar, space, bar, space, bar, space) for symbol values 0-105, then STOP. */
const PATTERNS: readonly string[] = [
  "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
  "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
  "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
  "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
  "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
  "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
  "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
  "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
  "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
  "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
  "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
  "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
  "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
  "211214", "211232", "2331112",
];

const START_B = 104;
const STOP = 106;

/** Thrown for a character subset B cannot carry. */
export class Code128Error extends Error {
  constructor(message: string) {
    super(message);
    this.name = "Code128Error";
  }
}

/** The symbol values for `text`, start and check character included, stop excluded. */
export function code128Values(text: string): number[] {
  if (text.length === 0) throw new Code128Error("Nothing to encode");
  const values = [START_B];
  let checksum = START_B;
  for (let i = 0; i < text.length; i += 1) {
    const point = text.charCodeAt(i);
    if (point < 32 || point > 126) {
      throw new Code128Error(`"${text[i]}" cannot be printed as Code 128`);
    }
    const value = point - 32;
    values.push(value);
    checksum += value * (i + 1);
  }
  values.push(checksum % 103);
  return values;
}

/** The module string — `"1"` a bar module, `"0"` a space module — for `text`. */
export function encodeCode128(text: string): string {
  let out = "";
  for (const value of [...code128Values(text), STOP]) {
    const widths = PATTERNS[value] as string;
    for (let i = 0; i < widths.length; i += 1) {
      out += (i % 2 === 0 ? "1" : "0").repeat(Number(widths[i]));
    }
  }
  return out;
}

/** Bars as `[x, width]` runs in module units, for drawing. */
export function code128Bars(text: string): { bars: [number, number][]; modules: number } {
  const modules = encodeCode128(text);
  const bars: [number, number][] = [];
  let start = -1;
  for (let i = 0; i <= modules.length; i += 1) {
    const on = modules[i] === "1";
    if (on && start === -1) start = i;
    if (!on && start !== -1) {
      bars.push([start, i - start]);
      start = -1;
    }
  }
  return { bars, modules: modules.length };
}
