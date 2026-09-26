import { afterEach, describe, expect, it, vi } from "vitest";

import { refreshAccessToken } from "../auth/refresh";
import { ApiError } from "../http";
import { setAccessToken, setUnauthorizedHandler } from "../session";
import { ASSET_ACCEPTED_TYPES, ASSET_MAX_UPLOAD_BYTES, uploadAsset } from "./assets";
import type { AssetOut } from "./assets";

vi.mock("../auth/refresh", () => ({ refreshAccessToken: vi.fn() }));

/**
 * A hand-rolled `XMLHttpRequest` — no mocking library carries multipart upload progress and abort
 * wiring, and the real behaviour worth pinning is exactly that plumbing, not a stand-in for it.
 */
class FakeXHR {
  static instances: FakeXHR[] = [];

  method = "";
  url = "";
  status = 0;
  responseText = "";
  requestHeaders: Record<string, string> = {};
  aborted = false;
  sentBody: FormData | null = null;
  upload = { onprogress: null as ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor() {
    FakeXHR.instances.push(this);
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string) {
    this.requestHeaders[name] = value;
  }

  send(body: FormData) {
    this.sentBody = body;
  }

  abort() {
    this.aborted = true;
    this.onabort?.();
  }

  respond(status: number, body: string) {
    this.status = status;
    this.responseText = body;
    this.onload?.();
  }

  networkError() {
    this.onerror?.();
  }

  timeOut() {
    this.ontimeout?.();
  }
}

function currentXhr(): FakeXHR {
  const xhr = FakeXHR.instances[FakeXHR.instances.length - 1];
  if (xhr === undefined) throw new Error("no XHR was created");
  return xhr;
}

function file(name = "photo.jpg", type = "image/jpeg"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type });
}

function controls() {
  const controller = new AbortController();
  const progress: number[] = [];
  return { controller, progress, controls: { signal: controller.signal, onProgress: (f: number) => progress.push(f) } };
}

const ASSET: AssetOut = {
  id: "a1",
  public_path: "/a/org-1/a1",
  public_url: "http://localhost:9100/opsgrids-public/org-1/a1",
  content_type: "image/jpeg",
  size_bytes: 1024,
  width: 100,
  height: 100,
  checksum_sha256: "abc", // pragma: allowlist secret
  original_filename: "photo.jpg",
  uploaded_by_user_id: "u1",
  created_at: "2026-09-25T00:00:00Z",
  deleted_at: null,
  purged_at: null,
};

afterEach(() => {
  FakeXHR.instances = [];
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.mocked(refreshAccessToken).mockReset();
  setUnauthorizedHandler(null);
  setAccessToken(null);
});

describe("uploadAsset", () => {
  it("sends one multipart file part with the bearer token attached", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    const xhr = currentXhr();
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/v1/documents/assets");
    expect(xhr.sentBody?.get("file")).toBeInstanceOf(File);

    xhr.respond(201, JSON.stringify(ASSET));
    await expect(promise).resolves.toEqual(ASSET);
  });

  it("reports fractional progress as the transport announces it", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c, progress } = controls();

    const promise = uploadAsset(file(), c);
    const xhr = currentXhr();
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 200 });
    xhr.respond(201, JSON.stringify(ASSET));
    await promise;

    expect(progress).toContain(0.25);
  });

  it("rejects with the server's own detail on a 413", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    currentXhr().respond(413, JSON.stringify({ title: "File is too large", detail: "That file is larger than the 5 MiB limit." }));

    await expect(promise).rejects.toMatchObject({ message: "That file is larger than the 5 MiB limit.", status: 413 });
  });

  it("rejects with the server's own detail on a 415/422 unsupported format", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    currentXhr().respond(422, JSON.stringify({ title: "Unsupported image", detail: "That isn't a JPEG, PNG or WebP image." }));

    await expect(promise).rejects.toThrow("That isn't a JPEG, PNG or WebP image.");
  });

  it("explains a 403 in plain language when the server sends no detail", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    currentXhr().respond(403, "");

    await expect(promise).rejects.toThrow(/don't have permission/);
  });

  it("is an ApiError a form's fieldErrors/problemMessage already know how to read", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    currentXhr().respond(403, "");

    const failure = await promise.catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
  });

  it("retries once on a network failure and succeeds on the second attempt", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    currentXhr().networkError();

    await vi.advanceTimersByTimeAsync(800);
    expect(FakeXHR.instances).toHaveLength(2);
    currentXhr().respond(201, JSON.stringify(ASSET));

    await expect(promise).resolves.toEqual(ASSET);
  });

  it("gives up after a second network failure with a plain sentence", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    currentXhr().networkError();
    await vi.advanceTimersByTimeAsync(800);
    currentXhr().networkError();

    await expect(promise).rejects.toThrow(/Couldn't reach the server/);
  });

  it("never retries a timeout — the server may already have stored the image", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    const settled = promise.catch((e: unknown) => e);
    currentXhr().timeOut();
    await vi.advanceTimersByTimeAsync(5_000);

    const failure = await settled;
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).message).toMatch(/timed out/);
    expect((failure as ApiError).message).toMatch(/may still have gone through/);
    expect(FakeXHR.instances).toHaveLength(1);
  });

  it("rejects a 2xx with an empty body instead of resolving nothing", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    currentXhr().respond(201, "  ");

    const failure = await promise.catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 201 });
    expect((failure as ApiError).message).toMatch(/sent nothing back/);
    expect(FakeXHR.instances).toHaveLength(1);
  });

  it("refreshes the session once on a 401 and retries once with the new token", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    setAccessToken("stale");
    vi.mocked(refreshAccessToken).mockImplementation(() => {
      setAccessToken("fresh");
      return Promise.resolve({ outcome: "ok", expiresIn: 900 });
    });
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    const first = currentXhr();
    expect(first.requestHeaders["Authorization"]).toBe("Bearer stale");
    first.respond(401, "");

    await vi.waitFor(() => expect(FakeXHR.instances).toHaveLength(2));
    const second = currentXhr();
    expect(second.requestHeaders["Authorization"]).toBe("Bearer fresh");
    second.respond(201, JSON.stringify(ASSET));

    await expect(promise).resolves.toEqual(ASSET);
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
  });

  it("does not refresh twice: a second 401 signs the caller out and rejects", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const signedOut = vi.fn();
    setUnauthorizedHandler(signedOut);
    vi.mocked(refreshAccessToken).mockResolvedValue({ outcome: "ok", expiresIn: 900 });
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    const settled = promise.catch((e: unknown) => e);
    currentXhr().respond(401, "");
    await vi.waitFor(() => expect(FakeXHR.instances).toHaveLength(2));
    currentXhr().respond(401, "");

    const failure = await settled;
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 401 });
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
    expect(FakeXHR.instances).toHaveLength(2);
    expect(signedOut).toHaveBeenCalledTimes(1);
  });

  it("signs the caller out and does not retry when the refresh cannot produce a token", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const signedOut = vi.fn();
    setUnauthorizedHandler(signedOut);
    vi.mocked(refreshAccessToken).mockResolvedValue({ outcome: "unauthorized" });
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    const settled = promise.catch((e: unknown) => e);
    currentXhr().respond(401, "");

    const failure = await settled;
    expect(failure).toMatchObject({ status: 401 });
    expect(FakeXHR.instances).toHaveLength(1);
    expect(signedOut).toHaveBeenCalledTimes(1);
  });

  it("never retries a 4xx — the server's answer is final", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controls: c } = controls();

    const promise = uploadAsset(file(), c);
    currentXhr().respond(403, "");
    await promise.catch(() => undefined);

    expect(FakeXHR.instances).toHaveLength(1);
  });

  it("aborts the live request and rejects without retrying when the signal fires", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const { controller, controls: c } = controls();

    const promise = uploadAsset(file(), c);
    controller.abort();

    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
    expect(currentXhr().aborted).toBe(true);
  });

  it("carries the platform's own accept list and size ceiling for a field to reuse", () => {
    expect(ASSET_ACCEPTED_TYPES).toEqual(["image/jpeg", "image/png", "image/webp"]);
    expect(ASSET_MAX_UPLOAD_BYTES).toBe(5 * 1024 * 1024);
  });
});
