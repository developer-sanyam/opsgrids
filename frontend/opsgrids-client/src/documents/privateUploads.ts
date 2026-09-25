/**
 * The `@opsgrids/ui` `FileUpload` adapter for the document service's **private** half
 * (`POST /documents/private-objects/initiate`, `:complete`, `GET`, `DELETE` — ADR-0009 §2, Q1).
 *
 * `FileUpload`'s own doc comment on `onUpload` names this file: "the only browser upload route
 * is the document service's public image library … a private file has no browser upload
 * contract yet." It does now, and the shape below is deliberately narrow — one function,
 * `uploadPrivateObject(purpose)`, that returns exactly what `onUpload` wants
 * (`(file, controls) => Promise<T>`), so a form drops it in without knowing anything about
 * presigned POSTs, MinIO, or the two round trips underneath.
 *
 * **Three requests, not one**, and a caller never sees the seam:
 * 1. `POST .../initiate` — this API, JSON, via `apiRequest` (bearer token, `problem+json`
 *    errors, the platform's ordinary shape).
 * 2. The presigned POST **straight to the bucket** — never through this API, never with a
 *    bearer token (a bucket does not have a session), over `XMLHttpRequest` rather than
 *    `fetch` because it is the one transport `FileUpload`'s `onProgress` can actually drive
 *    (`upload.onprogress`; `fetch` has no request-body progress event).
 * 3. `POST .../{id}/complete` — this API again, to verify what actually landed and get back
 *    the real, decoded `content_type`/`size_bytes`/`checksum_sha256`.
 *
 * A failure at step 2 never reaches this API at all — the bucket rejected the client's own
 * declared size or type before a byte of application code ran, exactly per ADR-0009's
 * "content-length-range" condition — so its message is generic rather than echoing anything
 * S3-shaped back at a user. Failures at 1 and 3 are `ApiError`s and carry the server's own
 * `problem.detail` verbatim, via `problemMessage`.
 */

import { apiRequest } from "../http";
import { problemMessage } from "../problem";

/** Structurally identical to `@opsgrids/ui`'s own `UploadControls` — not imported from there,
 *  because `@opsgrids/client` has no dependency on `@opsgrids/ui` and this file should not be
 *  the one to add it. TypeScript matches the two by shape, not by name. */
export interface PrivateUploadControls {
  signal: AbortSignal;
  onProgress: (fraction: number) => void;
}

/** What `uploadPrivateObject`'s returned function resolves with — enough for a form to submit
 *  the id and to render a `StoredFile` row (`@opsgrids/ui`) without a second request. */
export interface PrivateUploadResult {
  id: string;
  filename: string | null;
  contentType: string | null;
  sizeBytes: number | null;
}

interface InitiateResponse {
  id: string;
  upload_url: string;
  upload_fields: Record<string, string>;
  upload_expires_at: string;
}

interface PrivateObjectResponse {
  id: string;
  status: string;
  content_type: string | null;
  size_bytes: number | null;
  original_filename: string | null;
}

/** The purpose is validated server-side against `product.name` (`app/domain.py:PURPOSE_PATTERN`);
 *  nothing here re-checks it, so a malformed literal surfaces as the server's own 422, verbatim. */
function initiate(purpose: string, file: File): Promise<InitiateResponse> {
  return apiRequest<InitiateResponse>("/documents/private-objects/initiate", {
    method: "POST",
    body: {
      filename: file.name,
      content_type: file.type || "application/octet-stream",
      size_bytes: file.size,
      purpose,
    },
  });
}

/**
 * The presigned POST, direct to the bucket. Resolves on any 2xx/3xx; rejects with a plain
 * `Error` (never an `ApiError` — this response is not `problem+json`, it is S3/MinIO's own XML
 * error body, which is not this platform's contract to parse or to show).
 */
function submitToBucket(
  uploadUrl: string,
  fields: Record<string, string>,
  file: File,
  { signal, onProgress }: PrivateUploadControls,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    // Every field before the file — S3/MinIO's own POST-policy requirement; a field appended
    // after `file` is silently ignored by the bucket.
    for (const [key, value] of Object.entries(fields)) form.append(key, value);
    form.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", uploadUrl, true);

    const onAbort = () => xhr.abort();
    signal.addEventListener("abort", onAbort);
    const cleanup = () => signal.removeEventListener("abort", onAbort);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 400) resolve();
      else reject(new Error("The upload failed. Try again."));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error("The upload failed. Try again."));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new Error("Upload cancelled."));
    };
    xhr.send(form);
  });
}

function complete(id: string): Promise<PrivateObjectResponse> {
  return apiRequest<PrivateObjectResponse>(`/documents/private-objects/${id}/complete`, {
    method: "POST",
  });
}

/**
 * Build a `FileUpload`-shaped `onUpload` for one `purpose` (`"payroll.payslip"`,
 * `"hrms.id_scan"` — `"{product}.{name}"`, checked server-side).
 *
 * Usage, in a form:
 *
 *     <FileUpload
 *       label="ID proof"
 *       accept={["application/pdf", "image/*"]}
 *       onUpload={uploadPrivateObject("hrms.id_scan")}
 *       onChange={(values) => setFieldValue("idProofId", values[0]?.id)}
 *     />
 *
 * The uploaded file is **private** — reached only through a short-lived signed URL this API
 * issues after checking permission (`GET /documents/private-objects/{id}`), never a public
 * address. There is no `visibility` choice here the way the public asset picker warns about:
 * this route only ever writes to the private bucket.
 */
export function uploadPrivateObject(
  purpose: string,
): (file: File, controls: PrivateUploadControls) => Promise<PrivateUploadResult> {
  return async (file, controls) => {
    let initiated: InitiateResponse;
    try {
      initiated = await initiate(purpose, file);
    } catch (error) {
      throw new Error(problemMessage(error, "Could not start the upload. Try again."));
    }

    await submitToBucket(initiated.upload_url, initiated.upload_fields, file, controls);

    let completed: PrivateObjectResponse;
    try {
      completed = await complete(initiated.id);
    } catch (error) {
      // The bytes are already in the bucket at this point; `complete`'s own 415/413 means the
      // server looked at them and refused, which *is* the field-level message a user needs —
      // "That file is not one of the accepted types", not a generic retry prompt.
      throw new Error(
        problemMessage(error, "The uploaded file could not be verified. Try again."),
      );
    }

    return {
      id: completed.id,
      filename: completed.original_filename,
      contentType: completed.content_type,
      sizeBytes: completed.size_bytes,
    };
  };
}

/** `DELETE /documents/private-objects/{id}` — before it is attached to anything (409 once it
 *  is; this platform has no attach step exposed on this API yet, so every id `uploadPrivateObject`
 *  hands back is deletable until whatever product record it belongs to attaches it). Exported
 *  for a form's own "remove" affordance on a file it uploaded but has not saved yet. */
export async function deletePrivateObject(id: string): Promise<void> {
  try {
    await apiRequest<void>(`/documents/private-objects/${id}`, { method: "DELETE" });
  } catch (error) {
    throw new Error(problemMessage(error, "Could not remove the file. Try again."));
  }
}
