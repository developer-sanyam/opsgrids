import { describe, expect, it } from "vitest";

import { ASSET_BASE, resolveImageSrc } from "./assetSrc";

/**
 * The one rule this function exists to keep: **a stored value is never rewritten unless it is one
 * of ours.** A merchant's pasted `https://` address, the bundled `/img/…` art and a `blob:` preview
 * all have hosts already; only `/a/{org}/{asset}` is missing one, because ADR-0009 §6 deliberately
 * keeps the host out of merchant data so a bucket move or a CDN is a config change.
 *
 * Every case passes `base` explicitly. The default is read from the build's env and is asserted
 * once, separately — a suite that depended on it would be testing the `.env` file.
 */

const BASE = "https://cdn.example.test/opsgrids-public";
const ORG = "9a3f1c72-6b1e-4d0a-9a7f-2f5b8c1d4e60";
const ASSET = "3b7e9d01-4a2c-4f88-b5d1-77c0a9e2f331";
const PATH = `/a/${ORG}/${ASSET}`;

describe("resolveImageSrc", () => {
  /**
   * `/a` is a sentinel, not a path segment. `services/document`'s `storage_key` is
   * `{org}/{asset}` with no `a/` in it, so keeping the marker would address one segment deeper
   * than anything in the bucket and every uploaded image would 404 — which is exactly the kind of
   * agreement that is free to get wrong until the day there is a real upload to notice it with.
   */
  it("drops the sentinel and joins the object key onto the base", () => {
    expect(resolveImageSrc(PATH, BASE)).toBe(`${BASE}/${ORG}/${ASSET}`);
    expect(resolveImageSrc(PATH, BASE)).not.toContain("/a/");
  });

  it("leaves exactly one slash however the base was configured", () => {
    expect(resolveImageSrc(PATH, `${BASE}/`)).toBe(`${BASE}/${ORG}/${ASSET}`);
    expect(resolveImageSrc(PATH, `${BASE}///`)).toBe(`${BASE}/${ORG}/${ASSET}`);
  });

  it("passes an absolute URL through unchanged, so the URL box keeps working", () => {
    expect(resolveImageSrc("https://images.merchant.example/hero.jpg", BASE)).toBe(
      "https://images.merchant.example/hero.jpg",
    );
    expect(resolveImageSrc("http://images.merchant.example/hero.jpg", BASE)).toBe(
      "http://images.merchant.example/hero.jpg",
    );
  });

  it("passes the bundled sample art through unchanged", () => {
    expect(resolveImageSrc("/img/cat-staples.svg", BASE)).toBe("/img/cat-staples.svg");
  });

  it("does not rewrite an absolute URL that merely contains /a/", () => {
    const url = "https://images.merchant.example/a/hero.jpg";
    expect(resolveImageSrc(url, BASE)).toBe(url);
  });

  it("treats empty, blank and absent as no image", () => {
    expect(resolveImageSrc(null, BASE)).toBeNull();
    expect(resolveImageSrc("", BASE)).toBeNull();
    expect(resolveImageSrc("   ", BASE)).toBeNull();
  });

  it("treats a non-string setting as no image rather than coercing it", () => {
    expect(resolveImageSrc(0, BASE)).toBeNull();
    expect(resolveImageSrc(42, BASE)).toBeNull();
    expect(resolveImageSrc(true, BASE)).toBeNull();
    expect(resolveImageSrc(false, BASE)).toBeNull();
  });

  it("trims a pasted address so a stray space is not a broken image", () => {
    expect(resolveImageSrc("  https://images.merchant.example/hero.jpg  ", BASE)).toBe(
      "https://images.merchant.example/hero.jpg",
    );
    expect(resolveImageSrc(`  ${PATH}  `, BASE)).toBe(`${BASE}/${ORG}/${ASSET}`);
  });

  it("falls back to the app's own origin when no base is configured", () => {
    // No `VITE_ASSET_BASE_URL` in the test env, and the path stays root-relative rather than
    // acquiring a hard-coded localhost that a production bundle would then carry.
    expect(ASSET_BASE).toBe("");
    expect(resolveImageSrc(PATH)).toBe(`/${ORG}/${ASSET}`);
  });
});
