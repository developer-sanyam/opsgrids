import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ASSET_ACCEPT,
  ASSET_SVG_REFUSED,
  ASSET_WRONG_TYPE,
  MAX_ASSET_BYTES,
  UPLOAD_NEEDS_SERVICE,
  refuseAssetFile,
  uploadAsset,
} from "./client";
import { resolveImageSrc } from "@/theme/render/assetSrc";
import type { Asset } from "./types";

/**
 * The upload path, and specifically the two things it must never do: send a file the server was
 * always going to refuse, and pretend to have uploaded something it did not.
 */

function fileOf(name: string, type: string, bytes: number): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

const JPEG = () => fileOf("shopfront.jpg", "image/jpeg", 2048);

const KEY = "9a3f1c72-6b1e-4d0a-9a7f-2f5b8c1d4e60/3b7e9d01-4a2c-4f88-b5d1-77c0a9e2f331";

const ASSET: Asset = {
  id: "3b7e9d01-4a2c-4f88-b5d1-77c0a9e2f331",
  public_path: `/a/${KEY}`,
  public_url: `http://localhost:9100/opsgrids-public/${KEY}`,
  content_type: "image/jpeg",
  size_bytes: 204_800,
  width: 2560,
  height: 1440,
  checksum_sha256: "b1946ac92492d2347c6235b4d2611184e0d0b5b5a4b3f9d2c7a1e8f06d4c3b2a", // pragma: allowlist secret — a content digest, not a credential
  original_filename: "shopfront.jpg",
  uploaded_by_user_id: "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f",
  created_at: "2026-09-19T09:30:00Z",
  deleted_at: null,
  purged_at: null,
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("the two halves of the reference agree", () => {
  /**
   * The contract seam, pinned from this side.
   *
   * `services/document` computes `public_url` as `PUBLIC_ASSET_BASE_URL + "/" + storage_key`, and
   * `storage_key` is `{org}/{asset}` — no `a/` in it. `public_path` carries a leading `/a` purely
   * as a sentinel. Resolve the stored path against the same base and the two must land on the
   * same object; if they ever diverge, every uploaded image 404s and nothing on either side
   * fails a test of its own.
   */
  it("resolving public_path lands on the same address the server reports", () => {
    expect(resolveImageSrc(ASSET.public_path, "http://localhost:9100/opsgrids-public")).toBe(
      ASSET.public_url,
    );
  });
});

describe("what the picker offers", () => {
  it("accepts only the three formats the server will decode", () => {
    expect(ASSET_ACCEPT).toBe("image/jpeg,image/png,image/webp");
    expect(ASSET_ACCEPT).not.toContain("svg");
  });
});

describe("refuseAssetFile", () => {
  it("refuses a file over the 5 MiB limit and says how big it was", () => {
    const refusal = refuseAssetFile(fileOf("huge.jpg", "image/jpeg", MAX_ASSET_BYTES + 1));
    expect(refusal).toContain("over 5 MB");
    expect(refusal).toContain("5.0 MB");
  });

  it("accepts a file exactly on the limit — the boundary is not off by one", () => {
    expect(refuseAssetFile(fileOf("edge.jpg", "image/jpeg", MAX_ASSET_BYTES))).toBeNull();
  });

  it("refuses an SVG by type, and says it is not a limit that will be lifted", () => {
    expect(refuseAssetFile(fileOf("logo.svg", "image/svg+xml", 900))).toBe(ASSET_SVG_REFUSED);
  });

  it("refuses an SVG renamed to look like something else", () => {
    // A picker filtered to `accept` is not a check; a merchant can always choose "all files".
    expect(refuseAssetFile(fileOf("logo.svg", "", 900))).toBe(ASSET_SVG_REFUSED);
  });

  it("refuses a format the server does not decode", () => {
    expect(refuseAssetFile(fileOf("loop.gif", "image/gif", 900))).toBe(ASSET_WRONG_TYPE);
    expect(refuseAssetFile(fileOf("prices.pdf", "application/pdf", 900))).toBe(ASSET_WRONG_TYPE);
  });

  it("does not guess when the platform reports no type — the bytes decide, on the server", () => {
    expect(refuseAssetFile(fileOf("photo.jpeg", "", 900))).toBeNull();
  });

  it("passes a plain JPEG, PNG and WebP", () => {
    expect(refuseAssetFile(fileOf("a.jpg", "image/jpeg", 900))).toBeNull();
    expect(refuseAssetFile(fileOf("a.png", "image/png", 900))).toBeNull();
    expect(refuseAssetFile(fileOf("a.webp", "image/webp", 900))).toBeNull();
  });
});

describe("uploadAsset refuses before it spends a request", () => {
  it("never calls the network for an oversize file", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    await expect(uploadAsset(fileOf("huge.png", "image/png", MAX_ASSET_BYTES + 1))).rejects.toThrow(
      /over 5 MB/,
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("never calls the network for an SVG", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    await expect(uploadAsset(fileOf("logo.svg", "image/svg+xml", 400))).rejects.toThrow(
      ASSET_SVG_REFUSED,
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("fixtures mode", () => {
  /**
   * **It refuses, and it says why.** The tempting alternative is a `blob:` URL that makes the
   * editor look like it worked — and writes an address into the store document that dies with the
   * tab, shows nothing to anybody else and fails on reload with no explanation. This test is the
   * one that stops that being reintroduced as a convenience.
   */
  it("refuses with an explanation instead of faking an upload", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    await expect(uploadAsset(JPEG())).rejects.toThrow(UPLOAD_NEEDS_SERVICE);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("names the flag that turns it off, because only a developer ever sees this", () => {
    expect(UPLOAD_NEEDS_SERVICE).toContain("VITE_USE_FIXTURES=false");
  });

  it("hands back no address at all — nothing a caller could mistake for a stored one", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const outcome = await uploadAsset(JPEG()).then(
      (asset) => asset as Asset | null,
      () => null,
    );
    expect(outcome).toBeNull();
  });
});

/**
 * The live path. `USE_FIXTURES` is read at module load, so these re-import the module with the
 * flag stubbed — which is also the only way to prove the flag is what selects the branch.
 */
async function liveClient(): Promise<typeof import("./client")> {
  vi.stubEnv("VITE_USE_FIXTURES", "false");
  vi.resetModules();
  return import("./client");
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("uploadAsset against the gateway", () => {
  it("posts one multipart file part to the document service", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(201, ASSET));
    vi.stubGlobal("fetch", fetchSpy);

    const { uploadAsset: live } = await liveClient();
    const asset = await live(JPEG());

    expect(asset.public_path).toBe(ASSET.public_path);

    const call = fetchSpy.mock.calls[0];
    if (call === undefined) throw new Error("no request was made");
    const [url, init] = call as [string, RequestInit];

    expect(url).toBe("/api/v1/documents/assets");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const sent = (init.body as FormData).get("file");
    expect(sent).toBeInstanceOf(File);
    expect((sent as File).name).toBe("shopfront.jpg");

    // The browser must set `Content-Type` itself, or the multipart boundary will not match.
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("stores a root-relative path, never an absolute URL", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(201, ASSET)));
    const { uploadAsset: live } = await liveClient();

    const asset = await live(JPEG());
    expect(asset.public_path.startsWith("/a/")).toBe(true);
    expect(asset.public_path).not.toMatch(/^https?:/);
    // `public_url` exists for preview and is the one field that must never reach a document:
    // it embeds today's host, which is the whole thing `public_path` was invented to avoid.
    expect(asset.public_url).toMatch(/^https?:/);
  });

  it("prefers the server's own problem detail — it names the quota or the format", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          title: "Business rule violated",
          detail: "This organization has used 2.0 GiB of its 2.0 GiB of image storage.",
        }),
      ),
    );
    const { uploadAsset: live } = await liveClient();

    await expect(live(JPEG())).rejects.toThrow(/2.0 GiB of image storage/);
  });

  it("says something useful when the server refuses without a problem document", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 413 })));
    const { uploadAsset: live } = await liveClient();

    await expect(live(JPEG())).rejects.toThrow(/over 5 MB/);
  });

  it("explains a permission refusal rather than reporting a status code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 403 })));
    const { uploadAsset: live } = await liveClient();

    await expect(live(JPEG())).rejects.toThrow(/don't have permission/);
  });

  it("turns an unreachable network into a sentence with a remedy", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const { uploadAsset: live } = await liveClient();

    await expect(live(JPEG())).rejects.toThrow(/Check your connection/);
  });
});
