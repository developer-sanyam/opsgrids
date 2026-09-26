import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MetaSdkLoadError,
  loadMetaSdk,
  parseEmbeddedSignupMessage,
  signupLoginOptions,
} from "./metaSdk";
import type { FbSdk } from "./metaSdk";

function message(data: unknown, origin = "https://www.facebook.com"): MessageEvent {
  return new MessageEvent("message", { origin, data });
}

afterEach(() => {
  delete window.FB;
  delete window.fbAsyncInit;
  for (const script of document.head.querySelectorAll("script")) script.remove();
});

describe("parseEmbeddedSignupMessage", () => {
  const FINISH = {
    type: "WA_EMBEDDED_SIGNUP",
    event: "FINISH",
    data: { phone_number_id: "111", waba_id: "222" },
  };

  it("reads a finished signup from a JSON string or an object", () => {
    const expected = { kind: "finish", wabaId: "222", phoneNumberId: "111" };
    expect(parseEmbeddedSignupMessage(message(JSON.stringify(FINISH)))).toEqual(expected);
    expect(parseEmbeddedSignupMessage(message(FINISH))).toEqual(expected);
    expect(parseEmbeddedSignupMessage(message(FINISH, "https://web.facebook.com"))).toEqual(expected);
  });

  it("treats every FINISH variant as finished, with whatever ids it carries", () => {
    const only = { type: "WA_EMBEDDED_SIGNUP", event: "FINISH_ONLY_WABA", data: { waba_id: "222" } };
    expect(parseEmbeddedSignupMessage(message(only))).toEqual({ kind: "finish", wabaId: "222", phoneNumberId: null });
  });

  it("reads a cancellation and an error", () => {
    expect(
      parseEmbeddedSignupMessage(
        message({ type: "WA_EMBEDDED_SIGNUP", event: "CANCEL", data: { current_step: "PHONE_NUMBER_SETUP" } }),
      ),
    ).toEqual({ kind: "cancel", step: "PHONE_NUMBER_SETUP" });
    expect(
      parseEmbeddedSignupMessage(
        message({ type: "WA_EMBEDDED_SIGNUP", event: "ERROR", data: { error_message: "Session expired" } }),
      ),
    ).toEqual({ kind: "error", message: "Session expired" });
  });

  it("believes nobody but Meta's own origin", () => {
    expect(parseEmbeddedSignupMessage(message(FINISH, "https://evil.example.com"))).toBeNull();
    expect(parseEmbeddedSignupMessage(message(FINISH, "https://facebook.com.evil.example.com"))).toBeNull();
    expect(parseEmbeddedSignupMessage(message(FINISH, "http://www.facebook.com"))).toBeNull();
    expect(parseEmbeddedSignupMessage(message(FINISH, ""))).toBeNull();
  });

  it("ignores anything that is not a WA_EMBEDDED_SIGNUP message", () => {
    expect(parseEmbeddedSignupMessage(message("not json"))).toBeNull();
    expect(parseEmbeddedSignupMessage(message(JSON.stringify({ type: "OTHER", event: "FINISH" })))).toBeNull();
    expect(parseEmbeddedSignupMessage(message(42))).toBeNull();
    expect(parseEmbeddedSignupMessage(message(null))).toBeNull();
  });
});

describe("signupLoginOptions", () => {
  it("is exactly what the contract specifies", () => {
    expect(signupLoginOptions("cfg-1")).toEqual({
      config_id: "cfg-1",
      response_type: "code",
      override_default_response_type: true,
      extras: { setup: {}, featureType: "", sessionInfoVersion: "3" },
    });
  });
});

describe("loadMetaSdk", () => {
  const params = { appId: "123", graphVersion: "v21.0" };

  it("initialises an SDK that is already on the page, once", async () => {
    const init = vi.fn();
    window.FB = { init, login: vi.fn() } as unknown as FbSdk;
    await loadMetaSdk(params);
    await loadMetaSdk(params);
    expect(init).toHaveBeenCalledTimes(1);
    expect(init).toHaveBeenCalledWith({ appId: "123", autoLogAppEvents: true, xfbml: false, version: "v21.0" });
    expect(document.head.querySelector("script")).toBeNull();
  });

  it("adds Meta's script once for concurrent callers and resolves when it calls fbAsyncInit", async () => {
    const first = loadMetaSdk(params);
    const second = loadMetaSdk(params);
    expect(document.head.querySelectorAll('script[src="https://connect.facebook.net/en_US/sdk.js"]')).toHaveLength(1);

    const init = vi.fn();
    window.FB = { init, login: vi.fn() } as unknown as FbSdk;
    window.fbAsyncInit?.();
    const [a, b] = await Promise.all([first, second]);
    expect(a).toBe(b);
    expect(init).toHaveBeenCalledTimes(1);
  });

  it("rejects when the script cannot be fetched, and can be tried again", async () => {
    const failed = loadMetaSdk(params);
    document.head.querySelector("script")?.dispatchEvent(new Event("error"));
    await expect(failed).rejects.toBeInstanceOf(MetaSdkLoadError);
    expect(document.head.querySelector("script")).toBeNull();

    const again = loadMetaSdk(params);
    expect(document.head.querySelectorAll("script")).toHaveLength(1);
    window.FB = { init: vi.fn(), login: vi.fn() } as unknown as FbSdk;
    window.fbAsyncInit?.();
    await expect(again).resolves.toBeTruthy();
  });
});
