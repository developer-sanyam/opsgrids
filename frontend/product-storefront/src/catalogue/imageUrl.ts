/**
 * The image field's own validation — until `services/document` is deployed (it isn't on the
 * production box: no `document` container, no object store in `compose.prod.yml`), a product or
 * category image is a pasted `https://` address rather than an upload. This is the client-side
 * half of that rule; the server accepts any string up to 255 characters
 * (`schemas.py`'s `ImagePath`) and does not itself require `https://`, so this is a courtesy
 * that catches a pasted `http://` link or plain text before it is saved as an "image".
 */
export function isHttpsImageUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:";
  } catch {
    return false;
  }
}
