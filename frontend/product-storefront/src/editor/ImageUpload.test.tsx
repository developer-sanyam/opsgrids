import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Asset } from "@/api/types";
import type { SettingField, SettingValue } from "@/theme/schema";

import { SettingControl } from "./SettingControl";

/**
 * The part of the upload that only exists while a request is in flight: the busy state, the
 * refusal to send the same file twice, and what lands in the document when it succeeds.
 *
 * `uploadAsset` is replaced here — not to avoid the network, which `ImageControl.test.tsx` covers
 * with a spy, but because these assertions are about *timing*, and a resolved promise has no
 * in-flight moment to assert on. Everything else, including the refusals, runs against the real
 * client in the sibling file.
 */

const calls = vi.hoisted(() => ({
  count: 0,
  impl: (() => Promise.reject(new Error("no implementation set"))) as (file: File) => Promise<Asset>,
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    uploadAsset: (file: File) => {
      calls.count += 1;
      return calls.impl(file);
    },
  };
});

const KEY = "9a3f1c72-6b1e-4d0a-9a7f-2f5b8c1d4e60/3b7e9d01-4a2c-4f88-b5d1-77c0a9e2f331";
const PATH = `/a/${KEY}`;

const ASSET: Asset = {
  id: "3b7e9d01-4a2c-4f88-b5d1-77c0a9e2f331",
  public_path: PATH,
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

const FIELD: SettingField = { key: "image", label: "Image", type: "image", default: null };

function Harness({ initial = null }: { initial?: SettingValue }) {
  const [value, setValue] = useState<SettingValue>(initial);
  return <SettingControl field={FIELD} value={value} onChange={setValue} />;
}

function pick(name = "shopfront.jpg"): void {
  const input = screen.getByLabelText("Upload an image for Image");
  fireEvent.change(input, {
    target: { files: [new File([new Uint8Array(2048)], name, { type: "image/jpeg" })] },
  });
}

function urlBox(): HTMLInputElement {
  const box = screen.getByLabelText("Image");
  if (!(box instanceof HTMLInputElement)) throw new Error("the URL field is not a text input");
  return box;
}

/** A promise whose settling this test owns. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (cause: unknown) => void } {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  calls.count = 0;
  calls.impl = () => Promise.resolve(ASSET);
});

describe("while an upload is in flight", () => {
  it("says so, and disables the control that started it", async () => {
    const gate = deferred<Asset>();
    calls.impl = () => gate.promise;
    render(<Harness />);

    pick();

    const button = await screen.findByRole("button", { name: "Uploading…" });
    expect(button.hasAttribute("disabled")).toBe(true);
    // Announced politely rather than silently: nothing on this screen changes position, so a
    // sighted user sees the button and a screen-reader user is told.
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("Uploading…");

    gate.resolve(ASSET);
    await waitFor(() => expect(urlBox().value).toBe(PATH));
  });

  it("does not send the same file twice", async () => {
    const gate = deferred<Asset>();
    calls.impl = () => gate.promise;
    render(<Harness />);

    pick();
    await screen.findByRole("button", { name: "Uploading…" });
    pick();
    pick();

    expect(calls.count).toBe(1);

    gate.resolve(ASSET);
    await waitFor(() => expect(urlBox().value).toBe(PATH));
    expect(calls.count).toBe(1);
  });

  it("locks the URL box too, so two writers cannot race for the same setting", async () => {
    const gate = deferred<Asset>();
    calls.impl = () => gate.promise;
    render(<Harness />);

    pick();
    await screen.findByRole("button", { name: "Uploading…" });
    expect(urlBox().hasAttribute("disabled")).toBe(true);

    gate.resolve(ASSET);
    await waitFor(() => expect(urlBox().hasAttribute("disabled")).toBe(false));
  });
});

describe("when the upload succeeds", () => {
  it("writes the root-relative path into the setting, not an absolute URL", async () => {
    render(<Harness />);

    pick();

    await waitFor(() => expect(urlBox().value).toBe(PATH));
    expect(urlBox().value).not.toMatch(/^https?:/);
  });

  it("stops saying it is uploading", async () => {
    render(<Harness />);
    pick();

    await waitFor(() => expect(urlBox().value).toBe(PATH));
    expect(screen.getByRole("button", { name: "Upload" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("");
  });
});

describe("when the upload fails", () => {
  it("keeps the address the merchant had already typed", async () => {
    calls.impl = () => Promise.reject(new Error("The upload service isn't responding."));
    render(<Harness initial="https://images.merchant.example/hero.jpg" />);

    pick();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The upload service isn't responding.");
    expect(urlBox().value).toBe("https://images.merchant.example/hero.jpg");
  });

  it("lets the merchant try again immediately", async () => {
    calls.impl = () => Promise.reject(new Error("Couldn't reach the server."));
    render(<Harness />);

    pick();
    await screen.findByRole("alert");

    const button = screen.getByRole("button", { name: "Upload" });
    expect(button.hasAttribute("disabled")).toBe(false);

    calls.impl = () => Promise.resolve(ASSET);
    pick();
    await waitFor(() => expect(urlBox().value).toBe(PATH));
    expect(calls.count).toBe(2);
  });

  it("says something even when the failure carried no message", async () => {
    calls.impl = () => Promise.reject("nothing useful");
    render(<Harness />);

    pick();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The upload failed. Try again.");
  });
});
