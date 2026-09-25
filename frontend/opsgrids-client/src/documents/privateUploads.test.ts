import { afterEach, describe, expect, it, vi } from "vitest";

import { deletePrivateObject, uploadPrivateObject } from "./privateUploads";

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/** A fake `XMLHttpRequest` standing in for the presigned POST straight to the bucket — the one
 *  leg of the upload this adapter deliberately does not run through `fetch` (no request-body
 *  progress event on `fetch`). Configurable per test via `nextStatus`/`nextError`. */
class FakeXhr {
  static instances: FakeXhr[] = [];
  static nextStatus = 204;
  static failNetwork = false;
  /** Off for the abort test: `send()` records the call and waits, so the test controls exactly
   *  when (and whether) a response ever arrives, rather than racing a queued microtask. */
  static autoRespond = true;

  method = "";
  url = "";
  status = 0;
  sent: FormData | null = null;
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  aborted = false;

  constructor() {
    FakeXhr.instances.push(this);
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  abort() {
    this.aborted = true;
    this.onabort?.();
  }

  send(body: FormData) {
    this.sent = body;
    if (!FakeXhr.autoRespond) return;
    queueMicrotask(() => {
      if (FakeXhr.failNetwork) {
        this.onerror?.();
        return;
      }
      this.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 1 } as ProgressEvent);
      this.status = FakeXhr.nextStatus;
      this.onload?.();
    });
  }
}

function stubBucketUpload() {
  FakeXhr.instances = [];
  FakeXhr.nextStatus = 204;
  FakeXhr.failNetwork = false;
  FakeXhr.autoRespond = true;
  vi.stubGlobal("XMLHttpRequest", FakeXhr as unknown as typeof XMLHttpRequest);
}

const INITIATE_RESPONSE = {
  id: "obj-1",
  upload_url: "https://minio.test/opsgrids-private",
  upload_fields: { key: "org/payroll/obj-1", "Content-Type": "application/pdf" },
  upload_expires_at: "2026-01-01T00:00:00Z",
};

const COMPLETE_RESPONSE = {
  id: "obj-1",
  status: "stored",
  content_type: "application/pdf",
  size_bytes: 1234,
  original_filename: "payslip.pdf",
};

function file(name = "payslip.pdf", type = "application/pdf") {
  return new File(["%PDF-1.4"], name, { type });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadPrivateObject", () => {
  it("initiates, posts straight to the bucket, then completes — and resolves with the stored facts", async () => {
    stubBucketUpload();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(respond(201, INITIATE_RESPONSE))
      .mockResolvedValueOnce(respond(200, COMPLETE_RESPONSE));
    vi.stubGlobal("fetch", fetchMock);

    const onProgress = vi.fn();
    const controller = new AbortController();
    const result = await uploadPrivateObject("payroll.payslip")(file(), {
      signal: controller.signal,
      onProgress,
    });

    expect(result).toEqual({
      id: "obj-1",
      filename: "payslip.pdf",
      contentType: "application/pdf",
      sizeBytes: 1234,
    });

    // Two calls to this API — initiate and complete — never to the bucket.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const initiateCall = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(initiateCall[0]).toContain("/documents/private-objects/initiate");
    expect(JSON.parse(initiateCall[1].body as string)).toMatchObject({
      filename: "payslip.pdf",
      content_type: "application/pdf",
      purpose: "payroll.payslip",
    });
    const completeCall = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(completeCall[0]).toContain("/documents/private-objects/obj-1/complete");

    // The bucket call: a POST with the presigned fields ahead of the file, never through apiRequest.
    expect(FakeXhr.instances).toHaveLength(1);
    const xhr = FakeXhr.instances[0]!;
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe(INITIATE_RESPONSE.upload_url);
    expect(xhr.sent?.get("key")).toBe("org/payroll/obj-1");
    expect(xhr.sent?.get("file")).toBeInstanceOf(File);
    expect(onProgress).toHaveBeenCalledWith(1);
  });

  it("surfaces the server's own problem detail when initiate is refused", async () => {
    stubBucketUpload();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        respond(413, { title: "File too large", status: 413, detail: "That file is larger than the 20 MiB limit." }, {
          "content-type": "application/problem+json",
        }),
      ),
    );
    const controller = new AbortController();
    await expect(
      uploadPrivateObject("payroll.payslip")(file(), { signal: controller.signal, onProgress: vi.fn() }),
    ).rejects.toThrow("That file is larger than the 20 MiB limit.");
    expect(FakeXhr.instances).toHaveLength(0); // never reached the bucket
  });

  it("gives a generic message when the bucket itself refuses the upload", async () => {
    stubBucketUpload();
    FakeXhr.nextStatus = 400; // e.g. content-length-range violated
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(201, INITIATE_RESPONSE)));

    const controller = new AbortController();
    await expect(
      uploadPrivateObject("payroll.payslip")(file(), { signal: controller.signal, onProgress: vi.fn() }),
    ).rejects.toThrow("The upload failed. Try again.");
  });

  it("surfaces complete's own 415 when the bytes are refused after upload", async () => {
    stubBucketUpload();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(respond(201, INITIATE_RESPONSE))
        .mockResolvedValueOnce(
          respond(
            415,
            { title: "Unsupported file type", status: 415, detail: "That file is not one of the accepted types." },
            { "content-type": "application/problem+json" },
          ),
        ),
    );
    const controller = new AbortController();
    await expect(
      uploadPrivateObject("payroll.payslip")(file(), { signal: controller.signal, onProgress: vi.fn() }),
    ).rejects.toThrow("That file is not one of the accepted types.");
  });

  it("aborts the bucket upload when the signal fires", async () => {
    stubBucketUpload();
    // `send()` records the call and otherwise waits — abort is the only thing that ever
    // settles the bucket leg here, so there is no race with an auto-resolving timer.
    FakeXhr.autoRespond = false;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(201, INITIATE_RESPONSE)));
    const controller = new AbortController();
    const pending = uploadPrivateObject("payroll.payslip")(file(), {
      signal: controller.signal,
      onProgress: vi.fn(),
    });
    // Poll rather than guess a tick count: `initiate()`'s own await chain (fetch, then
    // `res.text()`) takes an unspecified number of microtasks before `submitToBucket`
    // constructs the `XMLHttpRequest` and calls `send()` on it.
    while (FakeXhr.instances.length === 0) {
      await Promise.resolve();
    }
    controller.abort();
    await expect(pending).rejects.toThrow("Upload cancelled.");
    expect(FakeXhr.instances[0]!.aborted).toBe(true);
  });
});

describe("deletePrivateObject", () => {
  it("calls DELETE and resolves with nothing on success", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(204, undefined));
    vi.stubGlobal("fetch", fetchMock);
    await expect(deletePrivateObject("obj-1")).resolves.toBeUndefined();
    const call = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(call[0]).toContain("/documents/private-objects/obj-1");
    expect(call[1].method).toBe("DELETE");
  });

  it("surfaces a 409 as a plain message — already attached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        respond(
          409,
          { title: "Conflict", status: 409, detail: "This file has already been attached and can no longer be deleted here." },
          { "content-type": "application/problem+json" },
        ),
      ),
    );
    await expect(deletePrivateObject("obj-1")).rejects.toThrow(
      "This file has already been attached and can no longer be deleted here.",
    );
  });
});
