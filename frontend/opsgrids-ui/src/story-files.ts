/**
 * Files for the workshop's upload stories, made in the browser at play time: no binary fixture
 * in the repository, nothing fetched, and the same pixels on every run (the visual harness
 * compares them). Colours come from the theme's own tokens, so a photo in dark mode is drawn in
 * the dark palette.
 */

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** A stand-in product photo: a bag, in one of three token colours. PNG, a couple of KB. */
export async function productPhoto(name: string, variant = 0): Promise<File> {
  const size = 160;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("This browser has no 2D canvas.");
  const colour = [token("--color-accent"), token("--color-info"), token("--color-cyan")][variant % 3] ?? token("--color-accent");

  context.fillStyle = token("--color-accent-soft");
  context.fillRect(0, 0, size, size);
  context.fillStyle = colour;
  context.beginPath();
  context.roundRect(36, 64, 88, 68, 12);
  context.fill();
  context.strokeStyle = colour;
  context.lineWidth = 8;
  context.beginPath();
  context.arc(80, 64, 22, Math.PI, 0);
  context.stroke();

  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (blob === null) throw new Error("The canvas produced no image.");
  return new File([blob], name, { type: "image/png" });
}

/** A file of a given type and size, for the refusals: the bytes are zeros, only the size matters. */
export function blankFile(name: string, type: string, bytes: number): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

/**
 * Drop files on an element the way a drag from the desktop does — the path a real user takes
 * that the picker's `accept` filter does not guard, which is why the refusal stories use it.
 */
export function dropFiles(target: Element, files: File[]): void {
  const transfer = new DataTransfer();
  for (const file of files) transfer.items.add(file);
  target.dispatchEvent(new DragEvent("dragenter", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  target.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
}
