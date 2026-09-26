/**
 * The document service's public asset library — `POST /api/v1/documents/assets` (ADR-0009). The
 * one browser-upload route the platform has today: one JPEG, PNG or WebP file, at most 5 MiB,
 * behind `core.content.asset.write`. **The result is world-readable by anyone who has its
 * address, forever, until purged** — a control that offers this upload must say so next to it,
 * not in a help page.
 *
 * Built on `XMLHttpRequest` rather than `apiRequest`/`fetch`, for two reasons neither can cover:
 * a multipart body must not carry `fetch`'s JSON `Content-Type` (the browser sets the boundary
 * itself), and only XHR's `upload.onprogress` reports upload progress cross-browser — `fetch`'s
 * request-body streaming has no equivalent. Shaped to drop straight into `FileUpload`'s
 * `onUpload={(file, controls) => …}`.
 *
 * **Retried once on a dropped connection or a DNS hiccup** — a failure with no response at all.
 * **A timeout is never retried**: the request may have reached the server and stored the image
 * before the client gave up, and a second attempt would then create a duplicate asset. A 4xx or
 * 5xx is the server's answer and is never retried either (the platform rule: an authorization
 * failure does not fix itself, and a write is not known idempotent from here), with one
 * exception: a 401 gets one silent session refresh through the client's own single-flight
 * refresh path, and one retry with the new token — the same treatment `apiRequest` gives every
 * other call, and safe because a 401 is refused before anything is stored. An abort (the caller
 * removed the file mid-upload) is never retried — it is not a failure, it is what was asked for.
 */

import { refreshAccessToken } from "../auth/refresh";
import { getAccessToken, notifyUnauthorized } from "../session";
import { ApiError, API_BASE } from "../http";
import type { ProblemJson } from "../http";

/** One asset in the organization's image library — `AssetOut` in `services/document/app/schemas.py`. */
export interface AssetOut {
  id: string;
  /**
   * **Store this.** Root-relative (`/a/{organization_id}/{asset_id}`) so the object store's host
   * — MinIO today, a CDN later — is config, not data baked into every merchant's record.
   */
  public_path: string;
  /** The absolute address, **for preview only**. Never persist it — it embeds today's host. */
  public_url: string;
  content_type: string;
  size_bytes: number;
  width: number;
  height: number;
  checksum_sha256: string;
  original_filename: string | null;
  uploaded_by_user_id: string | null;
  created_at: string;
  deleted_at: string | null;
  purged_at: string | null;
}

/** Matches `FileUpload`'s `UploadControls` structurally, without `@opsgrids/client` depending on
 *  `@opsgrids/ui` for one shape — neither package depends on the other today. */
export interface UploadAssetControls {
  signal: AbortSignal;
  onProgress: (fraction: number) => void;
}

/** The server's own ceiling (`services/document/app/config.py`'s `asset_max_upload_bytes`),
 *  mirrored so a field can refuse a huge file before it spends a request — a courtesy, never
 *  the authority; the server checks the real bytes regardless. */
export const ASSET_MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

/** The three formats the server decodes. Anything else — SVG included — is refused by the bytes,
 *  not the declared type, but there is no reason to let a merchant discover that after a round
 *  trip. */
export const ASSET_ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;

/** Every uploaded image is public the instant it lands — say so beside the control. */
export const ASSET_PUBLIC_HELPER =
  "JPEG, PNG or WebP, up to 5 MB. Uploaded images are public — anyone with the address can view them.";

/** How long one attempt waits before it gives up. Set beyond the server's own budget for 5 MiB
 *  (ADR-0009 §10's 20s), so a slow-but-working upload is never cut off by the client first. Giving
 *  up is final: the upload is reported as timed out, never retried (see the module comment). */
const UPLOAD_TIMEOUT_MS = 30_000;

function messageFor(problem: ProblemJson | null, status: number): string {
  if (problem?.detail) return problem.detail;
  if (problem?.title) return problem.title;
  if (status === 403) return "You don't have permission to upload images. An owner or admin can grant it.";
  if (status === 413) return "That file is too large. Choose one under 5 MB.";
  if (status === 415 || status === 422) return "Only JPEG, PNG and WebP images can be uploaded.";
  if (status === 429) return "Too many uploads at once. Wait a moment and try again.";
  if (status >= 500) return "The upload service isn't responding. Try again in a moment.";
  return `The upload failed (${status}).`;
}

function parseProblem(text: string): ProblemJson | null {
  if (text.trim() === "") return null;
  try {
    return JSON.parse(text) as ProblemJson;
  } catch {
    return null;
  }
}

/** Thrown only for a connection that failed outright, with no response — never for the server's own
 *  answer, which is always an `ApiError`, and never for a timeout, which is not known to have
 *  failed. Internal: `uploadAsset` retries this once and, failing that, surfaces it as an
 *  ordinary `ApiError` a form already knows how to show. */
class NetworkFailure extends Error {}

function abortError(): DOMException {
  return new DOMException("The upload was cancelled.", "AbortError");
}

/** Resolves once `ms` has passed, or immediately once `signal` aborts — never leaving a retry
 *  waiting on a file the caller already removed. */
function wait(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      resolve();
    }
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** One attempt, no retry — `uploadAsset` below is the retrying wrapper every caller should use. */
function attempt(file: File, signal: AbortSignal, onProgress: (fraction: number) => void): Promise<AssetOut> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }

    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file, file.name);

    function onAbort() {
      xhr.abort();
    }
    signal.addEventListener("abort", onAbort, { once: true });

    function cleanup() {
      signal.removeEventListener("abort", onAbort);
    }

    xhr.open("POST", `${API_BASE}/documents/assets`, true);
    xhr.timeout = UPLOAD_TIMEOUT_MS;
    xhr.setRequestHeader("Accept", "application/json");
    const token = getAccessToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };

    xhr.onload = () => {
      cleanup();
      const status = xhr.status;
      if (status >= 200 && status < 300) {
        // A 2xx with nothing in it is not a stored asset: resolving it would hand the form an
        // `undefined` to read `public_path` from. The upload may well have landed, so say that
        // rather than pretend it failed cleanly.
        if (xhr.responseText.trim() === "") {
          reject(
            new ApiError(
              status,
              "The server accepted the upload but sent nothing back to confirm it. Check the image library before uploading again.",
              null,
            ),
          );
          return;
        }
        try {
          resolve(JSON.parse(xhr.responseText) as AssetOut);
        } catch {
          reject(new ApiError(status, "The server's response couldn't be read.", null));
        }
        return;
      }
      const problem = parseProblem(xhr.responseText);
      reject(new ApiError(status, messageFor(problem, status), problem));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new NetworkFailure("Couldn't reach the server."));
    };
    xhr.ontimeout = () => {
      cleanup();
      // Not a `NetworkFailure`: a timeout says nothing about whether the server stored the image,
      // and retrying it is how a duplicate asset gets made.
      reject(
        new ApiError(
          0,
          "The upload timed out, and it may still have gone through. Check the image library before uploading again.",
          null,
        ),
      );
    };
    xhr.onabort = () => {
      cleanup();
      reject(abortError());
    };

    xhr.send(form);
  });
}

/**
 * One attempt, plus the session's one chance: a 401 gets a single silent refresh (the same
 * single-flight `refreshAccessToken` `apiRequest` uses) and one retry with whatever token that
 * produced. A refresh that cannot produce one, or a 401 on the retry itself, falls through to the
 * global signed-out handler exactly as `apiRequest` does — never a second refresh, never a loop.
 */
async function attemptWithSession(
  file: File,
  signal: AbortSignal,
  onProgress: (fraction: number) => void,
): Promise<AssetOut> {
  try {
    return await attempt(file, signal, onProgress);
  } catch (failure) {
    if (!(failure instanceof ApiError) || failure.status !== 401) throw failure;

    const refreshed = await refreshAccessToken();
    if (signal.aborted) throw abortError();
    if (refreshed.outcome !== "ok") {
      notifyUnauthorized();
      throw failure;
    }

    onProgress(0);
    try {
      return await attempt(file, signal, onProgress);
    } catch (retryFailure) {
      if (retryFailure instanceof ApiError && retryFailure.status === 401) notifyUnauthorized();
      throw retryFailure;
    }
  }
}

/** How long to wait before the one retry. Short: a dropped packet recovers fast, and a merchant
 *  watching a progress bar should not wait long to find out it is trying again. */
const RETRY_DELAY_MS = 800;

/**
 * Upload one image to the organization's asset library. Drop straight into `FileUpload`:
 *
 *     onUpload={(file, controls) => uploadAsset(file, controls)}
 *
 * Resolves with the stored `AssetOut` — write `asset.public_path` into the record, never
 * `asset.public_url`. Rejects with an `Error` whose `message` is the sentence to show: the
 * server's own `problem.detail` verbatim where it sent one, a plain-language fallback for the
 * status otherwise, or a "cancelled" message when `controls.signal` aborted first.
 */
export async function uploadAsset(file: File, controls: UploadAssetControls): Promise<AssetOut> {
  const { signal, onProgress } = controls;
  try {
    return await attemptWithSession(file, signal, onProgress);
  } catch (failure) {
    if (!(failure instanceof NetworkFailure) || signal.aborted) throw failure;

    await wait(RETRY_DELAY_MS, signal);
    if (signal.aborted) throw abortError();

    onProgress(0);
    try {
      return await attemptWithSession(file, signal, onProgress);
    } catch (retryFailure) {
      if (retryFailure instanceof NetworkFailure) {
        throw new ApiError(0, "Couldn't reach the server. Check your connection and try again.", null);
      }
      throw retryFailure;
    }
  }
}
