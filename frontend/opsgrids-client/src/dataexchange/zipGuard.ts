/**
 * A look inside the workbook's ZIP container before anything unpacks it.
 *
 * The 5 MB limit is on the compressed file, and a spreadsheet is deflated XML: a few kilobytes of
 * repeated cells can expand to gigabytes and freeze the tab (or the machine) inside the library
 * that unpacks them. The container declares every entry's compressed and uncompressed size in its
 * central directory, so those numbers are read here, from the bytes, and the file is refused when
 * they add up to more than an import could ever need. JSZip enforces the declared sizes when it
 * inflates, so an entry that lies about its size fails there instead of slipping past this check.
 *
 * The directory is read strictly. It has to end exactly where the end-of-central-directory record
 * begins, which is how every writer that matters lays it out; a file that disagrees is refused
 * rather than interpreted, because two readers that disagree about where the directory is are how
 * a check like this gets walked around.
 */

export const MAX_UNPACKED_BYTES = 40 * 1024 * 1024;
export const MAX_ENTRY_BYTES = 25 * 1024 * 1024;
/** An entry larger than this is held to `MAX_RATIO`; small ones are too small to hurt. */
export const RATIO_CHECK_ABOVE_BYTES = 1024 * 1024;
export const MAX_RATIO = 100;
export const MAX_ENTRIES = 2000;

const END_OF_CENTRAL_DIRECTORY = 0x06054b50;
const ZIP64_LOCATOR = 0x07064b50;
const CENTRAL_DIRECTORY_HEADER = 0x02014b50;
const END_RECORD_BYTES = 22;
const HEADER_BYTES = 46;
const MAX_COMMENT_BYTES = 0xffff;
const ZIP64_32 = 0xffffffff;
const ZIP64_16 = 0xffff;

export type ArchiveProblem = "too-large" | "malformed";

/** Null when the archive is within limits; otherwise what is wrong with it. */
export function inspectArchive(bytes: Uint8Array): ArchiveProblem | null {
  if (bytes.byteLength < END_RECORD_BYTES) return "malformed";
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  // The last end record in the file: the one the unpacking library will use.
  let end = -1;
  const lowest = Math.max(0, bytes.byteLength - END_RECORD_BYTES - MAX_COMMENT_BYTES);
  for (let at = bytes.byteLength - END_RECORD_BYTES; at >= lowest; at -= 1) {
    if (view.getUint32(at, true) === END_OF_CENTRAL_DIRECTORY) {
      end = at;
      break;
    }
  }
  if (end < 0) return "malformed";

  const disk = view.getUint16(end + 4, true);
  const directoryDisk = view.getUint16(end + 6, true);
  const entriesHere = view.getUint16(end + 8, true);
  const entries = view.getUint16(end + 10, true);
  const directorySize = view.getUint32(end + 12, true);
  const directoryOffset = view.getUint32(end + 16, true);

  const zip64Locator = end >= 20 && view.getUint32(end - 20, true) === ZIP64_LOCATOR;
  if (
    zip64Locator ||
    entries === ZIP64_16 ||
    entriesHere === ZIP64_16 ||
    directorySize === ZIP64_32 ||
    directoryOffset === ZIP64_32
  ) {
    return "too-large";
  }
  if (disk !== 0 || directoryDisk !== 0 || entries !== entriesHere) return "malformed";
  if (entries > MAX_ENTRIES) return "too-large";
  if (directoryOffset + directorySize !== end) return "malformed";

  let total = 0;
  let position = directoryOffset;
  for (let index = 0; index < entries; index += 1) {
    if (position + HEADER_BYTES > end) return "malformed";
    if (view.getUint32(position, true) !== CENTRAL_DIRECTORY_HEADER) return "malformed";
    const compressed = view.getUint32(position + 20, true);
    const uncompressed = view.getUint32(position + 24, true);
    const localOffset = view.getUint32(position + 42, true);
    if (compressed === ZIP64_32 || uncompressed === ZIP64_32 || localOffset === ZIP64_32) return "too-large";

    const next =
      position +
      HEADER_BYTES +
      view.getUint16(position + 28, true) +
      view.getUint16(position + 30, true) +
      view.getUint16(position + 32, true);
    if (next > end) return "malformed";
    position = next;

    if (uncompressed > MAX_ENTRY_BYTES) return "too-large";
    if (uncompressed > RATIO_CHECK_ABOVE_BYTES && uncompressed > MAX_RATIO * Math.max(compressed, 1)) {
      return "too-large";
    }
    total += uncompressed;
    if (total > MAX_UNPACKED_BYTES) return "too-large";
  }
  return position === end ? null : "malformed";
}
