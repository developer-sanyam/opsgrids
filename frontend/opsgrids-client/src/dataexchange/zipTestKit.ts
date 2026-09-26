/**
 * Builds ZIP containers by hand for the archive-guard tests: stored entries whose declared sizes
 * (and every other field of the end record) can be set to whatever a hostile file would claim.
 * Not exported from the package; nothing but the tests imports it.
 */

export interface ZipEntry {
  name: string;
  data?: Uint8Array;
  /** What the central directory claims, when it should differ from the data. */
  compressedSize?: number;
  uncompressedSize?: number;
}

export interface ZipOptions {
  /** The end record's entry counts, when they should differ from the number of entries. */
  declaredEntries?: number;
  entriesOnDisk?: number;
  disk?: number;
  directoryDisk?: number;
  /** Added to the directory offset / size the end record declares. */
  directoryOffsetShift?: number;
  directorySizeShift?: number;
  /** The directory offset / size the end record declares, whatever the real ones are. */
  directoryOffset?: number;
  directorySize?: number;
  /** Bytes between the central directory and the end record. */
  gap?: Uint8Array;
  comment?: Uint8Array;
  /** Puts a ZIP64 end-of-central-directory locator right before the end record. */
  zip64Locator?: boolean;
  /** Overwrites the 32-bit "local header offset" of every central directory entry. */
  localOffset?: number;
}

function bytesOf(size: number, write: (view: DataView) => void): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(size);
  write(new DataView(bytes.buffer));
  return bytes;
}

function join(parts: readonly Uint8Array[]): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

const encoder = new TextEncoder();

export function buildZip(entries: readonly ZipEntry[], options: ZipOptions = {}): Uint8Array<ArrayBuffer> {
  const local: Uint8Array[] = [];
  const directory: Uint8Array[] = [];
  let offset = 0;

  for (const entry of entries) {
    const name = encoder.encode(entry.name);
    const data = entry.data ?? new Uint8Array(0);
    const header = bytesOf(30, (view) => {
      view.setUint32(0, 0x04034b50, true);
      view.setUint16(4, 20, true);
      view.setUint32(18, data.length, true);
      view.setUint32(22, data.length, true);
      view.setUint16(26, name.length, true);
    });
    local.push(header, name, data);

    directory.push(
      bytesOf(46, (view) => {
        view.setUint32(0, 0x02014b50, true);
        view.setUint16(4, 20, true);
        view.setUint16(6, 20, true);
        view.setUint32(20, entry.compressedSize ?? data.length, true);
        view.setUint32(24, entry.uncompressedSize ?? data.length, true);
        view.setUint16(28, name.length, true);
        view.setUint32(42, options.localOffset ?? offset, true);
      }),
      name,
    );
    offset += header.length + name.length + data.length;
  }

  const central = join(directory);
  const gap = options.gap ?? new Uint8Array(0);
  const comment = options.comment ?? new Uint8Array(0);
  const declared = options.declaredEntries ?? entries.length;
  const locator = options.zip64Locator === true
    ? bytesOf(20, (view) => {
        view.setUint32(0, 0x07064b50, true);
      })
    : new Uint8Array(0);
  const end = bytesOf(22, (view) => {
    view.setUint32(0, 0x06054b50, true);
    view.setUint16(4, options.disk ?? 0, true);
    view.setUint16(6, options.directoryDisk ?? 0, true);
    view.setUint16(8, options.entriesOnDisk ?? declared, true);
    view.setUint16(10, declared, true);
    view.setUint32(12, options.directorySize ?? central.length + (options.directorySizeShift ?? 0), true);
    view.setUint32(16, options.directoryOffset ?? offset + (options.directoryOffsetShift ?? 0), true);
    view.setUint16(20, comment.length, true);
  });

  return join([...local, central, gap, locator, end, comment]);
}
