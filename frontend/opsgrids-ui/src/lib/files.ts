/**
 * The pure half of `FileUpload`: sizes in words, and whether a file is one the field takes. The
 * browser's `accept` attribute only filters the picker — a drag and drop, or "All files" in the
 * picker, ignores it — so every file is checked here as well. And the server checks again: this
 * is the fast "no", not the authority.
 */

const UNITS = ["bytes", "KB", "MB", "GB"] as const;

/** 5242880 → "5 MB", 250880 → "245 KB", 1258291 → "1.2 MB". Binary units, as file managers show. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return bytes === 1 ? "1 byte" : `${bytes} bytes`;
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = value < 10 && !Number.isInteger(Math.round(value * 10) / 10) ? value.toFixed(1) : Math.round(value).toString();
  return `${rounded} ${UNITS[unit]}`;
}

/** The short name people know a format by. */
const FORMAT_NAME: Record<string, string> = {
  "image/png": "PNG",
  "image/jpeg": "JPG",
  "image/webp": "WebP",
  "image/gif": "GIF",
  "image/svg+xml": "SVG",
  "image/*": "image",
  "application/pdf": "PDF",
  "text/csv": "CSV",
  "text/plain": "text",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
};

function formatName(entry: string): string {
  if (entry.startsWith(".")) return entry.slice(1).toUpperCase();
  return FORMAT_NAME[entry] ?? entry;
}

/** ["image/png", "image/jpeg", "image/webp"] → "PNG, JPG or WebP". Duplicates (".jpg", "image/jpeg") fold. */
export function describeAccept(accept: readonly string[]): string {
  const names = [...new Set(accept.map(formatName))];
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

/**
 * Does the file match one entry of an `accept` list? The same grammar as the attribute: an
 * extension (".pdf"), an exact type ("image/png"), or a family ("image/*").
 */
export function acceptsFile(file: { name: string; type: string }, accept: readonly string[]): boolean {
  if (accept.length === 0) return true;
  const name = file.name.toLowerCase();
  const type = file.type.toLowerCase();
  return accept.some((entry) => {
    const rule = entry.toLowerCase();
    if (rule.startsWith(".")) return name.endsWith(rule);
    if (rule.endsWith("/*")) return type.startsWith(rule.slice(0, -1));
    return type === rule;
  });
}

/** Why a file cannot be added, as the sentence the user reads — or null when it can. */
export function rejectionFor(
  file: { name: string; type: string; size: number },
  { accept, maxSize }: { accept: readonly string[]; maxSize: number | undefined },
): string | null {
  if (!acceptsFile(file, accept)) return `${file.name} isn't a ${describeAccept(accept)} file.`;
  if (maxSize !== undefined && file.size > maxSize) {
    return `${file.name} is ${formatBytes(file.size)}; the limit is ${formatBytes(maxSize)}.`;
  }
  return null;
}
