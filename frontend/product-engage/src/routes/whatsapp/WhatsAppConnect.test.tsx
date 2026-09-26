import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Page, WhatsAppConnectionOut } from "@/api/types";
import * as api from "@/api/whatsappSignup";
import type { FbLoginResponse, FbSdk } from "@/lib/metaSdk";
import {
  APPROVED_TEMPLATE,
  CONFIG_DISABLED,
  CONFIG_ENABLED,
  HEALTH_DISCONNECTED,
  HEALTH_OK,
  MAIN_NUMBER,
  PENDING_NUMBER,
  PENDING_TEMPLATE,
  SETUP_DONE,
  SETUP_FAILED,
  SETUP_ID,
  SETUP_RUNNING,
  SUPPORT_NUMBER,
  templatePage,
} from "./testFixtures";
import { WhatsAppConnectPage } from "./WhatsAppConnect";

const GRANTS = ["engage.setup.whatsapp.read:ORG", "engage.setup.whatsapp.write:ORG"];

const CONNECTION: WhatsAppConnectionOut = {
  waba_id: "102938475617890",
  phone_number_id: MAIN_NUMBER.phone_number_id,
  display_number: "+91 98765 43210",
  status: "connected",
};

function connectionPage(): Page<WhatsAppConnectionOut> {
  return { items: [CONNECTION], total: 1, page: 1, size: 200, pages: 1 };
}

function Probe() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function renderWizard(url = "/whatsapp/connect", grants: string[] = GRANTS) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[url]}>
        <WhatsAppConnectPage />
        <Probe />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function search(): string {
  return screen.getByTestId("search").textContent ?? "";
}

/** A stand-in for Meta's SDK: `init` and `login` are spies, and the login callback is kept so a
 *  test can play the popup's outcome. */
let loginCallback: ((response: FbLoginResponse) => void) | null = null;
let sdk: { init: ReturnType<typeof vi.fn>; login: ReturnType<typeof vi.fn> };

function stubSdk() {
  loginCallback = null;
  sdk = {
    init: vi.fn(),
    login: vi.fn((callback: (response: FbLoginResponse) => void) => {
      loginCallback = callback;
    }),
  };
  window.FB = sdk as unknown as FbSdk;
}

/** What Meta's popup posts to the opener. */
function postFromMeta(data: unknown, origin = "https://www.facebook.com") {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { origin, data: JSON.stringify(data) }));
  });
}

const FINISH = {
  type: "WA_EMBEDDED_SIGNUP",
  event: "FINISH",
  data: { phone_number_id: MAIN_NUMBER.phone_number_id, waba_id: CONNECTION.waba_id },
};

function playLogin(response: FbLoginResponse) {
  act(() => {
    if (loginCallback === null) throw new Error("FB.login was never called");
    loginCallback(response);
  });
}

async function startMeta(user: ReturnType<typeof userEvent.setup>) {
  const button = await screen.findByRole("button", { name: /Continue with Meta/ });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  await user.click(button);
}

beforeEach(() => {
  stubSdk();
  vi.spyOn(api, "getSignupConfig").mockResolvedValue(CONFIG_ENABLED);
  vi.spyOn(client, "getWhatsAppConnection").mockResolvedValue(connectionPage());
});

afterEach(() => {
  delete window.FB;
  vi.useRealTimers();
});

describe("connect step", () => {
  it("shows a skeleton while it asks whether Meta sign-in is available", () => {
    vi.spyOn(api, "getSignupConfig").mockReturnValue(new Promise(() => {}));
    const { container } = renderWizard();
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Continue with Meta/ })).toBeNull();
  });

  it("says what went wrong, and retries, when the config read fails", async () => {
    const spy = vi
      .spyOn(api, "getSignupConfig")
      .mockRejectedValueOnce(new ApiError(500, "Internal server error", null))
      .mockResolvedValue(CONFIG_ENABLED);
    const user = userEvent.setup();
    renderWizard();
    expect(await screen.findByText("Couldn't check whether Meta sign-in is available")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Connect your WhatsApp Business Account" })).toBeTruthy();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("shows a not-configured state, with no Meta button, when the server has no Meta app", async () => {
    vi.spyOn(api, "getSignupConfig").mockResolvedValue(CONFIG_DISABLED);
    renderWizard();
    expect(await screen.findByRole("heading", { name: "Connecting with Meta isn't set up yet" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Continue with Meta/ })).toBeNull();
    const manual = screen.getByRole("link", { name: "Enter credentials manually" });
    expect(manual.getAttribute("href")).toBe("/whatsapp/connection");
    // Nothing was loaded from Meta for a server that cannot use it.
    expect(window.FB).toBe(sdk as unknown as FbSdk);
    expect(sdk.init).not.toHaveBeenCalled();
  });

  it("renders the approved copy and initialises the SDK with the server's app and version", async () => {
    renderWizard();
    expect(await screen.findByRole("heading", { name: "Connect your WhatsApp Business Account" })).toBeTruthy();
    expect(screen.getByText("Have these ready")).toBeTruthy();
    expect(screen.getByText("OpsGrids then does this for you")).toBeTruthy();
    expect(screen.getByText("Only an Owner or Admin can connect WhatsApp.")).toBeTruthy();
    await waitFor(() =>
      expect(sdk.init).toHaveBeenCalledWith({
        appId: CONFIG_ENABLED.app_id,
        autoLogAppEvents: true,
        xfbml: false,
        version: "v21.0",
      }),
    );
  });

  it("is refused for a caller who cannot connect WhatsApp", async () => {
    renderWizard("/whatsapp/connect", ["engage.setup.whatsapp.read:ORG"]);
    expect(await screen.findByText("You can't open WhatsApp setup right now")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Continue with Meta/ })).toBeNull();
  });
});

describe("the Meta window", () => {
  it("opens FB.login with the contract's options inside the click, and moves to the Meta step", async () => {
    const user = userEvent.setup();
    renderWizard();
    await startMeta(user);

    expect(sdk.login).toHaveBeenCalledWith(expect.any(Function), {
      config_id: CONFIG_ENABLED.config_id,
      response_type: "code",
      override_default_response_type: true,
      extras: { setup: {}, featureType: "", sessionInfoVersion: "3" },
    });
    expect(await screen.findByRole("heading", { name: "Finish in the Meta window" })).toBeTruthy();
    expect(search()).toContain("step=meta");
    expect(screen.getByText("Waiting for Meta. Nothing needs to be done on this page.")).toBeTruthy();
  });

  it("posts the code with the ids from Meta's message, then moves on with only the setup id in the URL", async () => {
    const complete = vi.spyOn(api, "completeSignup").mockResolvedValue({ setup_id: SETUP_ID, status: "running" });
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    const user = userEvent.setup();
    renderWizard();
    await startMeta(user);

    postFromMeta(FINISH);
    playLogin({ authResponse: { code: "AQB-single-use-code" } });

    await waitFor(() =>
      expect(complete).toHaveBeenCalledWith({
        code: "AQB-single-use-code",
        waba_id: CONNECTION.waba_id,
        phone_number_id: MAIN_NUMBER.phone_number_id,
      }),
    );
    expect(await screen.findByRole("heading", { name: "Choose the number to use" })).toBeTruthy();
    expect(search()).toContain("step=select");
    expect(search()).toContain(`setup=${SETUP_ID}`);
    expect(search()).not.toContain("AQB");
    expect(complete).toHaveBeenCalledTimes(1);
  });

  it("goes on with the code alone when Meta's message never arrives", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const complete = vi.spyOn(api, "completeSignup").mockResolvedValue({ setup_id: SETUP_ID, status: "running" });
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderWizard();
    await startMeta(user);

    playLogin({ authResponse: { code: "AQB-code-only" } });
    expect(complete).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(complete).toHaveBeenCalledWith({ code: "AQB-code-only" });
  });

  it("ignores a WA_EMBEDDED_SIGNUP message from any origin but Meta's", async () => {
    const complete = vi.spyOn(api, "completeSignup").mockResolvedValue({ setup_id: SETUP_ID, status: "running" });
    const user = userEvent.setup();
    renderWizard();
    await startMeta(user);

    postFromMeta({ ...FINISH, event: "CANCEL" }, "https://evil.example.com");
    playLogin({ authResponse: { code: "AQB-code" } });
    postFromMeta(FINISH, "https://facebook.com.evil.example.com");

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(complete).not.toHaveBeenCalled();
    expect(screen.queryByText("The Meta window was closed before setup finished")).toBeNull();
  });

  it("says so when the window is closed before Meta finishes, and lets it be reopened", async () => {
    const user = userEvent.setup();
    renderWizard();
    await startMeta(user);

    playLogin({ status: "unknown", authResponse: null });
    expect(await screen.findByText("The Meta window was closed before setup finished")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /Reopen Meta window/ }));
    expect(sdk.login).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Waiting for Meta. Nothing needs to be done on this page.")).toBeTruthy();
  });

  it("treats Meta's own CANCEL message as a cancellation", async () => {
    const user = userEvent.setup();
    renderWizard();
    await startMeta(user);

    postFromMeta({ type: "WA_EMBEDDED_SIGNUP", event: "CANCEL", data: { current_step: "PHONE_NUMBER_SETUP" } });
    expect(await screen.findByText("The Meta window was closed before setup finished")).toBeTruthy();
  });

  it("says so when the browser blocks the popup", async () => {
    // Assigned, not spied: the sign-in code observes `window.open` by reassigning it, and a spy's
    // setter would swallow that. `null` is what a browser returns for a refused popup.
    const realOpen = window.open;
    window.open = () => null;
    sdk.login.mockImplementation(() => {
      window.open("https://www.facebook.com/dialog/oauth");
    });
    try {
      const user = userEvent.setup();
      renderWizard();
      await startMeta(user);

      expect(await screen.findByText("Your browser blocked the Meta window")).toBeTruthy();
      expect(window.open).toBeTypeOf("function");
    } finally {
      window.open = realOpen;
    }
  });

  it("shows the server's sentence, and keeps the way back, when signup/complete is refused", async () => {
    vi.spyOn(api, "completeSignup").mockRejectedValue(new ApiError(400, "That code has expired.", null));
    const user = userEvent.setup();
    renderWizard();
    await startMeta(user);

    postFromMeta(FINISH);
    playLogin({ authResponse: { code: "AQB-expired" } });

    expect(await screen.findByText("Setup couldn't be started")).toBeTruthy();
    expect(screen.getByText(/That code has expired\./)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Reopen Meta window/ })).toBeTruthy();
    expect(search()).toContain("step=meta");
  });

  it("says the window isn't open when the page is reloaded on this step", async () => {
    renderWizard("/whatsapp/connect?step=meta");
    expect(await screen.findByText("The Meta window isn't open")).toBeTruthy();
    expect(sdk.login).not.toHaveBeenCalled();
  });

  it("does not claim Meta has confirmed anything when 'I have finished' is pressed too early", async () => {
    const user = userEvent.setup();
    renderWizard();
    await startMeta(user);
    await user.click(screen.getByRole("button", { name: "I have finished" }));
    expect(screen.getByText(/Meta hasn't confirmed yet/)).toBeTruthy();
    expect(search()).toContain("step=meta");
  });

  it("offers the SDK load failure a retry instead of a dead button", async () => {
    delete window.FB;
    const user = userEvent.setup();
    renderWizard();
    const button = await screen.findByRole("button", { name: /Continue with Meta/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    const script = await waitFor(() => {
      const found = document.head.querySelector('script[src*="connect.facebook.net"]');
      if (found === null) throw new Error("script not added");
      return found;
    });
    act(() => {
      script.dispatchEvent(new Event("error"));
    });
    expect(await screen.findByText("Meta's sign-in couldn't be loaded")).toBeTruthy();

    stubSdk();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect((screen.getByRole("button", { name: /Continue with Meta/ }) as HTMLButtonElement).disabled).toBe(false));
  });
});

describe("choose a number", () => {
  const SELECT_URL = `/whatsapp/connect?step=select&setup=${SETUP_ID}`;

  it("waits, in a live region, while Meta's numbers have not arrived", async () => {
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [] });
    renderWizard(SELECT_URL);
    expect(await screen.findByText("Waiting for Meta to share your numbers…")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Use this number" }).hasAttribute("disabled")).toBe(true);
  });

  it("lists every number with its badge, disables the ones that cannot be used, and saves the choice", async () => {
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({
      items: [MAIN_NUMBER, SUPPORT_NUMBER, PENDING_NUMBER],
    });
    const setDefault = vi.spyOn(api, "setDefaultNumber").mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderWizard(SELECT_URL);

    const group = await screen.findByRole("group", { name: "Phone number" });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect((radios[0] as HTMLInputElement).checked).toBe(true);
    expect((radios[2] as HTMLInputElement).disabled).toBe(true);
    expect(within(group).getByText("Ready")).toBeTruthy();
    expect(within(group).getByText("Display name in review")).toBeTruthy();
    expect(within(group).getByText("Needs verification")).toBeTruthy();
    expect(within(group).getByText("1,000 customers / 24 hours")).toBeTruthy();
    expect(screen.getByText("ID ••••7890")).toBeTruthy();

    await user.click(radios[1] as HTMLElement);
    await user.click(screen.getByRole("button", { name: "Use this number" }));
    await waitFor(() => expect(setDefault).toHaveBeenCalledWith(SUPPORT_NUMBER.id));
    expect(search()).toContain("step=setup");
  });

  it("shows the server's sentence and stays put when choosing fails", async () => {
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    vi.spyOn(api, "setDefaultNumber").mockRejectedValue(new ApiError(409, "That number is no longer connected.", null));
    const user = userEvent.setup();
    renderWizard(SELECT_URL);

    await user.click(await screen.findByRole("button", { name: "Use this number" }));
    expect(await screen.findByText("That number is no longer connected.")).toBeTruthy();
    expect(search()).toContain("step=select");
  });

  it("shows a real error, with a retry, when the numbers cannot be read", async () => {
    vi.spyOn(api, "listWhatsAppNumbers").mockRejectedValue(new ApiError(403, "Forbidden", null));
    renderWizard(SELECT_URL);
    expect(await screen.findByText("Couldn't load your phone numbers")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("goes back to the start when the setup id is missing", async () => {
    renderWizard("/whatsapp/connect?step=select");
    expect(await screen.findByRole("heading", { name: "Connect your WhatsApp Business Account" })).toBeTruthy();
    expect(search()).toBe("?step=start");
  });
});

describe("setting things up", () => {
  const SETUP_URL = `/whatsapp/connect?step=setup&setup=${SETUP_ID}`;

  it("polls the job until it is done, then lets the customer continue to the finished screen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const poll = vi
      .spyOn(api, "getSignupStatus")
      .mockResolvedValueOnce(SETUP_RUNNING)
      .mockResolvedValue(SETUP_DONE);
    vi.spyOn(api, "getWhatsAppHealth").mockResolvedValue(HEALTH_OK);
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(templatePage([APPROVED_TEMPLATE]));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderWizard(SETUP_URL);

    expect(await screen.findByText("3 of 5 steps done")).toBeTruthy();
    expect(poll).toHaveBeenCalledWith(SETUP_ID);
    expect(screen.getByText("18 of 24 templates imported.")).toBeTruthy();
    expect(screen.getByText("In progress")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Setup progress" }).getAttribute("aria-valuenow")).toBe("60");
    expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(await screen.findByText("5 of 5 steps done")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Setup progress" }).getAttribute("aria-valuenow")).toBe("100");

    const callsWhenDone = poll.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(poll.mock.calls.length).toBe(callsWhenDone);

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "WhatsApp is connected" })).toBeTruthy();
    expect(search()).toContain("step=done");
  });

  it("stops polling and offers a fresh start when the job fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const poll = vi
      .spyOn(api, "getSignupStatus")
      .mockResolvedValueOnce(SETUP_RUNNING)
      .mockResolvedValue(SETUP_FAILED);
    renderWizard(SETUP_URL);

    expect(await screen.findByText("3 of 5 steps done")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });

    expect(await screen.findByText("Setup didn't finish")).toBeTruthy();
    expect(screen.getAllByText(/Meta refused the webhook subscription\./).length).toBeGreaterThan(0);
    expect(screen.getByText(/webhook_subscribe_failed/)).toBeTruthy();
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();

    const calls = poll.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(poll.mock.calls.length).toBe(calls);

    fireEvent.click(screen.getByRole("button", { name: "Start again" }));
    expect(await screen.findByRole("heading", { name: "Connect your WhatsApp Business Account" })).toBeTruthy();
    expect(search()).toBe("?step=start");
  });

  it("keeps the last answer on screen and says it is stale when a later read fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, "getSignupStatus")
      .mockResolvedValueOnce(SETUP_RUNNING)
      .mockRejectedValue(new TypeError("Failed to fetch"));
    renderWizard(SETUP_URL);

    expect(await screen.findByText("3 of 5 steps done")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(await screen.findByText("Couldn't refresh the setup status")).toBeTruthy();
    expect(screen.getByText("3 of 5 steps done")).toBeTruthy();
  });

  it("does not retry a 4xx", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const poll = vi.spyOn(api, "getSignupStatus").mockRejectedValue(new ApiError(404, "No such setup", null));
    renderWizard(SETUP_URL);

    expect(await screen.findByText("Couldn't read the setup status")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(poll).toHaveBeenCalledTimes(1);
  });
});

describe("done", () => {
  const DONE_URL = "/whatsapp/connect?step=done";

  function mockConnected() {
    vi.spyOn(api, "getWhatsAppHealth").mockResolvedValue(HEALTH_OK);
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [SUPPORT_NUMBER, MAIN_NUMBER] });
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(templatePage([APPROVED_TEMPLATE, PENDING_TEMPLATE]));
  }

  it("shows what the server reports, not what the wizard assumed", async () => {
    mockConnected();
    renderWizard(DONE_URL);

    expect(await screen.findByRole("heading", { name: "WhatsApp is connected" })).toBeTruthy();
    expect(screen.getByText("Acme Corp · +91 ••••• ••210")).toBeTruthy();
    expect(screen.getByText("Acme Corp (approved)")).toBeTruthy();
    expect(screen.getByText("High")).toBeTruthy();
    expect(screen.getByText("1,000 customers / 24 hours")).toBeTruthy();
    expect(screen.getByText("24 synced from Meta")).toBeTruthy();
    const next = screen.getByRole("complementary", { name: "Next steps" });
    expect(within(next).getByRole("link", { name: /Review your 24 templates/ }).getAttribute("href")).toBe("/whatsapp/templates");
    expect(within(next).getByRole("link", { name: "Go to WhatsApp overview" }).getAttribute("href")).toBe("/whatsapp");
  });

  it("says it is not connected when the server says so", async () => {
    vi.spyOn(api, "getWhatsAppHealth").mockResolvedValue(HEALTH_DISCONNECTED);
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [] });
    renderWizard(DONE_URL);
    expect(await screen.findByRole("heading", { name: "WhatsApp isn't connected yet" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "WhatsApp is connected" })).toBeNull();
  });

  it("shows an error with a retry when the connection cannot be checked", async () => {
    vi.spyOn(api, "getWhatsAppHealth").mockRejectedValue(new ApiError(500, "Internal server error", null));
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [] });
    renderWizard(DONE_URL);
    expect(await screen.findByText("Couldn't check the connection")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("offers only approved templates and sends the test message to an E.164 number", async () => {
    mockConnected();
    const send = vi.spyOn(api, "sendWhatsAppTestMessage").mockResolvedValue({ message_id: "b1b2b3b4-0000-4000-8000-000000000009" });
    const user = userEvent.setup();
    renderWizard(DONE_URL);

    await screen.findByRole("heading", { name: "Send a test message" });
    const template = await screen.findByLabelText("Template");
    expect(within(template).getByRole("option", { name: "hello_world (Utility, English)" })).toBeTruthy();
    expect(within(template).queryByRole("option", { name: /order_shipped/ })).toBeNull();

    await user.type(screen.getByLabelText(/Send to/), "+91 98765 43210");
    await user.click(screen.getByRole("button", { name: "Send test message" }));

    await waitFor(() =>
      expect(send).toHaveBeenCalledWith({ to: "+919876543210", template_name: "hello_world", language: "en" }),
    );
    expect(await screen.findByText("Test message sent")).toBeTruthy();
  });

  it("checks the number before it asks the server, and keeps what was typed when the server refuses", async () => {
    mockConnected();
    const send = vi
      .spyOn(api, "sendWhatsAppTestMessage")
      .mockRejectedValue(new ApiError(409, "This organization has no connected WhatsApp channel.", null));
    const user = userEvent.setup();
    renderWizard(DONE_URL);

    const to = await screen.findByLabelText(/Send to/);
    await user.type(to, "98765");
    await user.click(screen.getByRole("button", { name: "Send test message" }));
    expect(screen.getByText("Enter the number with its country code, like +91 98765 43210.")).toBeTruthy();
    expect(to.getAttribute("aria-invalid")).toBe("true");
    expect(send).not.toHaveBeenCalled();

    await user.clear(to);
    await user.type(to, "+91 98765 43210");
    await user.click(screen.getByRole("button", { name: "Send test message" }));
    expect(await screen.findByText("This organization has no connected WhatsApp channel.")).toBeTruthy();
    expect((screen.getByLabelText(/Send to/) as HTMLInputElement).value).toBe("+91 98765 43210");
  });

  it("says there is nothing to test with when no template is approved", async () => {
    vi.spyOn(api, "getWhatsAppHealth").mockResolvedValue(HEALTH_OK);
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(templatePage([PENDING_TEMPLATE]));
    renderWizard(DONE_URL);
    expect(await screen.findByText("No approved templates yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send test message" })).toBeNull();
  });
});

describe("accessibility", () => {
  it("names the progress list and marks exactly the current stage", async () => {
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    renderWizard(`/whatsapp/connect?step=select&setup=${SETUP_ID}`);

    const list = await screen.findByRole("list", { name: "Setup progress" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    const current = items.filter((item) => item.getAttribute("aria-current") === "step");
    expect(current).toHaveLength(1);
    expect(current[0]?.textContent).toContain("Choose number");
    expect(items[0]?.textContent).toContain("completed");
  });

  it("moves focus to the step's heading when the step changes", async () => {
    const user = userEvent.setup();
    renderWizard();
    await startMeta(user);
    const heading = await screen.findByRole("heading", { name: "Finish in the Meta window" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it("has one h1 per step and names every control", async () => {
    renderWizard();
    await screen.findByRole("heading", { level: 1, name: "Connect your WhatsApp Business Account" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const link = screen.getByRole("link", { name: /How this works/ });
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(screen.getByRole("link", { name: "Exit setup" })).toBeTruthy();
  });
});
