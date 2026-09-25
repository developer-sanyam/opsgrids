import { useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, ReactNode } from "react";

import { ArrowUp, CloseIcon, FileTextIcon } from "../icons";
import { cn } from "../lib/cn";
import { FIELD_LABEL } from "../lib/field";
import { describeAccept, formatBytes, rejectionFor } from "../lib/files";
import { Button } from "./Button";

/** What `onUpload` is given besides the file: a way to be cancelled, and a way to report progress. */
export interface UploadControls {
  /** Aborted when the user removes the file mid-upload. Pass it to `fetch` or `xhr.abort()`. */
  signal: AbortSignal;
  /** Report progress as a fraction, 0 to 1. If it is never called, the bar shows work without a figure. */
  onProgress: (fraction: number) => void;
}

/** A file stored before this form opened — an edit form's existing attachment or photo. */
export interface StoredFile<T> {
  name: string;
  size?: number | undefined;
  /** A thumbnail address, for an image. */
  previewUrl?: string | undefined;
  /** What `onChange` reports for it: the same kind of value `onUpload` resolves with. */
  value: T;
}

interface FileUploadProps<T> {
  label: string;
  /**
   * The rule, under the prompt. Generated from `accept`, `maxSize` and `maxFiles` when omitted
   * ("PNG, JPG or WebP, up to 5 MB"). Pass your own where the field needs a warning the rule
   * cannot carry — an image library's "Anyone with the link can see uploaded images."
   */
  helper?: ReactNode | undefined;
  /** The same grammar as the `accept` attribute: ".pdf", "image/png", "image/*". Empty takes anything. */
  accept?: string[] | undefined;
  /** Bytes. */
  maxSize?: number | undefined;
  /** Default 1. With 1, a second file replaces the first — the avatar and logo case. */
  maxFiles?: number | undefined;
  /**
   * Sends one file wherever this form's backend takes files, and resolves with what the form
   * should submit for it (an asset id, a `public_path`). Rejects with an `Error` whose message is
   * the sentence to show — the server's problem `detail`, verbatim. Honour `signal`; call
   * `onProgress` if the transport can (XHR's `upload.onprogress` can, `fetch` cannot).
   *
   * The adapter belongs in `@opsgrids/client`, beside the API it calls. Today the only browser
   * upload route is the document service's public image library (`POST /documents/assets`,
   * ADR-0009: JPEG/PNG/WebP up to 5 MiB, **world-readable**). A private file — a contract, a
   * spec sheet — has no browser upload contract yet (ADR-0009 Q1), so do not point this at one.
   */
  onUpload: (file: File, controls: UploadControls) => Promise<T>;
  /** Every stored value, in order, whenever the set changes — what the form submits. */
  onChange?: ((values: T[]) => void) | undefined;
  initialFiles?: StoredFile<T>[] | undefined;
  /** The field-level error from the form's own validation or a 422, verbatim. */
  error?: string | undefined;
  required?: boolean | undefined;
  optional?: boolean | undefined;
  disabled?: boolean | undefined;
  /** The drop zone's call to action. Default "Choose a file" / "Choose files". */
  prompt?: string | undefined;
  className?: string | undefined;
}

type Status = "uploading" | "done" | "failed" | "rejected";

interface Item<T> {
  id: string;
  name: string;
  size: number | undefined;
  file: File | null;
  previewUrl: string | null;
  /** An object URL this component made, and so must revoke. */
  ownsPreview: boolean;
  status: Status;
  /** 0–1, or null while the transport has not said. */
  progress: number | null;
  message: string | null;
  result: { value: T } | null;
  controller: AbortController | null;
}

let sequence = 0;

function previewFor(file: File): string | null {
  if (!file.type.startsWith("image/") || typeof URL.createObjectURL !== "function") return null;
  return URL.createObjectURL(file);
}

function releasePreview(item: { ownsPreview: boolean; previewUrl: string | null }): void {
  if (item.ownsPreview && item.previewUrl !== null && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(item.previewUrl);
  }
}

function statusLine(item: Item<unknown>): string {
  const size = item.size !== undefined ? formatBytes(item.size) : null;
  const state =
    item.status === "uploading"
      ? item.progress === null
        ? "Uploading…"
        : `Uploading… ${Math.round(item.progress * 100)}%`
      : item.status === "done"
        ? "Uploaded"
        : null;
  return [size, state].filter(Boolean).join(" · ");
}

/**
 * Drag and drop or click to choose, with a preview per image, a size and type check before
 * anything is sent, progress while it is, and a Retry when it fails (brief §8–9: file uploads).
 *
 * **Transport-agnostic.** The field calls `onUpload(file, { signal, onProgress })` once per
 * accepted file and keeps whatever it resolves with; it knows nothing about endpoints. Uploads
 * start as soon as a file is chosen, so a long form's Save only sends the returned values.
 *
 * **Keyboard and screen reader.** The drop zone is a real button — Enter or Space opens the
 * picker — named by the label and its prompt, and described by the rule and any error. Dropping
 * is a pointer shortcut to the same place. Every file is a row with its state in words and its
 * own Remove (and Retry) button; what happens to each file is announced once, politely. Removing
 * a file puts focus back on the drop zone instead of letting it fall to the page.
 */
export function FileUpload<T>({
  label,
  helper,
  accept = [],
  maxSize,
  maxFiles = 1,
  onUpload,
  onChange,
  initialFiles = [],
  error,
  required = false,
  optional = false,
  disabled = false,
  prompt,
  className,
}: FileUploadProps<T>) {
  const base = useId();
  const labelId = `${base}-label`;
  const promptId = `${base}-prompt`;
  const hintId = `${base}-hint`;
  const requiredId = required ? `${base}-required` : undefined;
  const errorId = error ? `${base}-error` : undefined;
  const inputRef = useRef<HTMLInputElement>(null);
  const zoneRef = useRef<HTMLButtonElement>(null);
  const [dragging, setDragging] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [items, setItems] = useState<Item<T>[]>(() =>
    initialFiles.map((stored) => ({
      id: `stored-${++sequence}`,
      name: stored.name,
      size: stored.size,
      file: null,
      previewUrl: stored.previewUrl ?? null,
      ownsPreview: false,
      status: "done",
      progress: 1,
      message: null,
      result: { value: stored.value },
      controller: null,
    })),
  );

  // The latest callbacks and items, for work that finishes after the render that started it.
  const latest = useRef({ onUpload, onChange, items });
  useEffect(() => {
    latest.current = { onUpload, onChange, items };
  });

  // Report the stored set whenever it changes — not on mount, where it is what the form began with.
  const reported = useRef(items.filter((item) => item.status === "done").map((item) => item.id).join("|"));
  useEffect(() => {
    const done = items.filter((item) => item.status === "done");
    const signature = done.map((item) => item.id).join("|");
    if (signature === reported.current) return;
    reported.current = signature;
    latest.current.onChange?.(done.flatMap((item) => (item.result ? [item.result.value] : [])));
  }, [items]);

  // Unmounting mid-upload cancels, and every preview this field made is let go.
  useEffect(
    () => () => {
      for (const item of latest.current.items) {
        item.controller?.abort();
        releasePreview(item);
      }
    },
    [],
  );

  function update(id: string, patch: Partial<Item<T>>) {
    setItems((previous) => previous.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  function start(item: Item<T>) {
    const file = item.file;
    if (file === null) return;
    const controller = new AbortController();
    update(item.id, { status: "uploading", progress: null, message: null, controller });
    latest.current
      .onUpload(file, {
        signal: controller.signal,
        onProgress: (fraction) => {
          if (!controller.signal.aborted) update(item.id, { progress: Math.min(Math.max(fraction, 0), 1) });
        },
      })
      .then((value) => {
        if (controller.signal.aborted) return;
        update(item.id, { status: "done", progress: 1, result: { value }, controller: null });
        setAnnouncement(`${item.name} uploaded.`);
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        const message = failure instanceof Error && failure.message ? failure.message : "The upload failed. Try again.";
        update(item.id, { status: "failed", message, controller: null });
        setAnnouncement(`${item.name} couldn't be uploaded. ${message}`);
      });
  }

  function add(files: File[]) {
    if (disabled || files.length === 0) return;
    const replacing = maxFiles === 1;
    const kept = replacing ? [] : items.filter((item) => item.status !== "rejected");
    let room = maxFiles - kept.length;
    const added: Item<T>[] = files.map((file) => {
      const rejection =
        rejectionFor(file, { accept, maxSize }) ??
        (room <= 0 ? `${file.name} wasn't added: ${maxFiles} files is the most this field takes.` : null);
      if (rejection === null) room -= 1;
      return {
        id: `file-${++sequence}`,
        name: file.name,
        size: file.size,
        file: rejection === null ? file : null,
        previewUrl: rejection === null ? previewFor(file) : null,
        ownsPreview: rejection === null,
        status: rejection === null ? "uploading" : "rejected",
        progress: null,
        message: rejection,
        result: null,
        controller: null,
      };
    });
    if (replacing) {
      for (const item of items) {
        item.controller?.abort();
        releasePreview(item);
      }
    }
    // Rejections from an earlier pick have been read; a new pick replaces them.
    setItems([...kept, ...added]);
    const rejected = added.filter((item) => item.status === "rejected");
    if (rejected.length > 0) setAnnouncement(rejected.map((item) => item.message).join(" "));
    for (const item of added) if (item.status === "uploading") start(item);
  }

  function remove(item: Item<T>) {
    item.controller?.abort();
    releasePreview(item);
    setItems((previous) => previous.filter((candidate) => candidate.id !== item.id));
    setAnnouncement(`${item.name} removed.`);
    zoneRef.current?.focus();
  }

  function onPick(event: ChangeEvent<HTMLInputElement>) {
    add(Array.from(event.target.files ?? []));
    // Cleared, so choosing the same file again (after removing it) still fires a change.
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    setDragging(false);
    add(Array.from(event.dataTransfer.files));
  }

  function onDragLeave(event: DragEvent<HTMLButtonElement>) {
    // Leaving for one of the zone's own children is not leaving the zone.
    if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
    setDragging(false);
  }

  const stored = items.filter((item) => item.status !== "rejected").length;
  const full = maxFiles > 1 && stored >= maxFiles;
  const hint =
    helper ??
    [
      accept.length > 0 ? describeAccept(accept) : "Any file type",
      maxSize !== undefined ? `up to ${formatBytes(maxSize)}${maxFiles > 1 ? " each" : ""}` : null,
      maxFiles > 1 ? `${maxFiles} files at most` : null,
    ]
      .filter(Boolean)
      .join(", ");

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-baseline gap-1">
        <span id={labelId} className={FIELD_LABEL}>
          {label}
          {optional ? (
            <>
              {" "}
              <span className="normal-case text-faint">(optional)</span>
            </>
          ) : null}
        </span>
        {required ? (
          <>
            <span aria-hidden className="text-sm font-bold leading-none text-negative">
              *
            </span>
            <span id={requiredId} className="sr-only">
              Required.
            </span>
          </>
        ) : null}
      </div>

      <button
        ref={zoneRef}
        type="button"
        disabled={disabled || full}
        aria-labelledby={`${labelId} ${promptId}`}
        // `aria-required` is not allowed on a button, so "Required" is part of its description.
        aria-describedby={[requiredId, hintId, errorId].filter(Boolean).join(" ")}
        {...(error ? { "aria-invalid": true } : {})}
        data-dragging={dragging || undefined}
        onClick={() => inputRef.current?.click()}
        onDragEnter={(event) => {
          event.preventDefault();
          if (!disabled && !full) setDragging(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-tile border border-dashed px-4 py-6 text-center",
          "bg-surface transition-colors",
          error ? "border-negative" : "border-line",
          // Dragging over: an accent edge and the ground fill, on which every line of the zone's
          // text still holds contrast (the accent tint would sink the muted rule below AA).
          "enabled:hover:border-accent data-dragging:border-accent data-dragging:bg-ground",
          "disabled:cursor-not-allowed disabled:opacity-60",
        )}
      >
        <span aria-hidden className="grid size-10 place-items-center rounded-full bg-accent-soft text-accent-ink">
          <ArrowUp className="size-5" />
        </span>
        <span id={promptId} className="text-sm font-semibold text-accent-ink">
          {full ? "All files added" : (prompt ?? (maxFiles > 1 ? "Choose files" : "Choose a file"))}
          {full ? null : <span className="hidden font-medium text-ink-soft md:inline"> or drag and drop</span>}
        </span>
        <span id={hintId} className="text-xs text-muted">
          {hint}
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        hidden
        tabIndex={-1}
        aria-hidden
        multiple={maxFiles > 1}
        {...(accept.length > 0 ? { accept: accept.join(",") } : {})}
        onChange={onPick}
      />

      {error ? (
        <p id={errorId} className="text-xs font-medium text-negative">
          {error}
        </p>
      ) : null}

      {items.length > 0 ? (
        <ul aria-label={`${label}: files`} className="mt-1 flex flex-col gap-2">
          {items.map((item) => {
            const problem = item.status === "failed" || item.status === "rejected";
            return (
              <li
                key={item.id}
                className={cn(
                  "flex items-center gap-3 rounded-tile border bg-surface p-2 pr-3",
                  problem ? "border-negative" : "border-line",
                )}
              >
                {item.previewUrl !== null ? (
                  <img src={item.previewUrl} alt="" className="size-12 shrink-0 rounded-tile object-cover" />
                ) : (
                  <span aria-hidden className="grid size-12 shrink-0 place-items-center rounded-tile bg-ground text-muted">
                    <FileTextIcon className="size-5" />
                  </span>
                )}
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <p className="truncate text-sm font-medium text-ink">{item.name}</p>
                  {problem ? (
                    <p className="text-xs font-medium text-negative">{item.message}</p>
                  ) : (
                    <p className="tnum text-xs text-muted">{statusLine(item)}</p>
                  )}
                  {item.status === "uploading" ? (
                    <div
                      role="progressbar"
                      aria-label={`Uploading ${item.name}`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      {...(item.progress !== null ? { "aria-valuenow": Math.round(item.progress * 100) } : {})}
                      className="h-1.5 overflow-hidden rounded-full bg-ground"
                    >
                      <div
                        className={cn("h-full rounded-full bg-accent", item.progress === null && "w-1/3 animate-pulse")}
                        {...(item.progress !== null ? { style: { width: `${item.progress * 100}%` } } : {})}
                      />
                    </div>
                  ) : null}
                </div>
                {item.status === "failed" && item.file !== null ? (
                  <Button variant="outline" size="sm" onClick={() => start(item)} disabled={disabled}>
                    Retry
                  </Button>
                ) : null}
                <button
                  type="button"
                  onClick={() => remove(item)}
                  disabled={disabled}
                  aria-label={`${item.status === "rejected" ? "Dismiss" : "Remove"} ${item.name}`}
                  className="grid size-9 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-ground hover:text-ink disabled:cursor-not-allowed"
                >
                  <CloseIcon className="size-4" />
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>
    </div>
  );
}
