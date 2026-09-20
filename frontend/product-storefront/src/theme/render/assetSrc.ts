/**
 * Where an image address points.
 *
 * A merchant's image can arrive from two places and only one of them is ours. An uploaded asset is
 * stored in the document as a **root-relative path** — `/a/{organization_id}/{asset_id}`
 * (ADR-0009 §6) — because an absolute URL would bake a hostname into merchant data, and moving
 * MinIO → S3 → a CDN would then be a data migration over customer-authored content. A pasted
 * address is already absolute and belongs to whoever hosts it. The bundled sample art under
 * `/img/…` is served by this app itself.
 *
 * So the only thing worth abstracting is the host of *our* assets, and this is the single place a
 * stored value becomes an `<img src>`. Pure, no DOM, no config read at call time: `base` is a
 * parameter with a default, which is what makes the rule testable without a build.
 */

import type { SettingValue } from "../schema";

/**
 * The marker an uploaded asset's stored path always carries — and it is a **sentinel, not a path
 * segment**. `/a/{org}/{asset}` means "this is one of ours"; the object key under `ASSET_BASE` is
 * `{org}/{asset}`, so resolving means dropping the `/a` and joining the rest. Keeping it would
 * produce `…/opsgrids-public/a/{org}/{asset}`, one segment deeper than anything in the bucket, and
 * every uploaded image would 404 — visibly, but only after a real upload existed to notice it
 * with. `services/document/app/domain.py::public_path` is the other half of this agreement.
 */
export const ASSET_PATH_PREFIX = "/a/";

/**
 * The origin uploaded assets are served from — the bucket in dev, the CDN in production.
 *
 * `VITE_ASSET_BASE_URL=http://localhost:9100/opsgrids-public` locally. **Empty by default and
 * deliberately not defaulted to localhost**: a stray dev default compiled into a production bundle
 * would point every merchant's imagery at a machine that is not there. Empty means "same origin as
 * this app", which is the correct answer once a CDN or an ingress fronts `/a/` — and a visibly
 * broken image rather than a silently wrong host when neither is configured.
 */
export const ASSET_BASE: string = (import.meta.env.VITE_ASSET_BASE_URL ?? "").trim();

/**
 * Turn a stored setting value into something an `<img src>` can use.
 *
 * - `/a/{org}/{asset}` — an uploaded asset. The sentinel is dropped and `{org}/{asset}` is joined
 *   onto `base`, which is the object's key in the bucket.
 * - Anything else non-empty — passed through unchanged. That covers an absolute `https://` address
 *   a merchant pasted, the bundled `/img/…` art, and `data:`/`blob:` values, none of which we have
 *   any business rewriting.
 * - Empty, whitespace, `null`, a number or a boolean — `null`, which is the signal every caller
 *   already understands as "draw the generated plate instead".
 */
export function resolveImageSrc(value: SettingValue, base: string = ASSET_BASE): string | null {
  if (typeof value !== "string") return null;

  const trimmed = value.trim();
  if (trimmed === "") return null;

  if (!trimmed.startsWith(ASSET_PATH_PREFIX)) return trimmed;

  const key = trimmed.slice(ASSET_PATH_PREFIX.length);
  // One slash between the two halves, whichever way the base was configured. An empty base leaves
  // a root-relative `/{org}/{asset}`, which is the same-origin reading of "no host configured".
  return `${base.replace(/\/+$/, "")}/${key}`;
}
