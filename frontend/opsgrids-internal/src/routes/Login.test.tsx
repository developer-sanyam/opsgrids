import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as authApi from "@/auth/api";
import { AuthProvider } from "@/auth/AuthProvider";
import { ApiError } from "@/api/http";
import type { OperatorMe, OperatorTokenOut } from "@/auth/types";
import { LoginPage } from "./Login";

/**
 * The operator sign-in end to end, against the auth-service contract this app now actually
 * implements (`services/auth/app/api/routes.py`'s `operator_login` / `mfa/enrol` /
 * `mfa/enrol/confirm` / `mfa/verify`): a password never earns a session by itself, only which
 * second-factor step comes next, and every failure the server distinguishes with a status code is
 * shown verbatim and handled differently — a wrong code retries, a lock returns to the start, and
 * the 503 re-seal problem says plainly that a recovery code still works.
 */

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const ME: OperatorMe = {
  staff_id: "op_1",
  email: "b@opsgrids.com",
  display_name: "B Test",
  operator_roles: ["owner"],
  amr: ["pwd", "otp"],
};

const TOKEN: OperatorTokenOut = {
  status: "complete",
  access_token: "tok",
  token_type: "Bearer",
  expires_in: 900,
  operator_roles: ["owner"],
};

async function signInToCode() {
  vi.spyOn(authApi, "login").mockResolvedValue({ status: "mfa_required", pre_auth_token: "pat" });
  renderLogin();
  await userEvent.type(screen.getByLabelText("Operator email"), "b@opsgrids.com");
  await userEvent.type(screen.getByLabelText("Password"), "hunter2");
  await userEvent.click(screen.getByRole("button", { name: "Sign in to console" }));
  await screen.findByText("Enter your code");
}

async function signInToEnrolCode() {
  vi.spyOn(authApi, "login").mockResolvedValue({
    status: "mfa_enrollment_required",
    pre_auth_token: "pat",
  });
  renderLogin();
  await userEvent.type(screen.getByLabelText("Operator email"), "b@opsgrids.com");
  await userEvent.type(screen.getByLabelText("Password"), "hunter2");
  await userEvent.click(screen.getByRole("button", { name: "Sign in to console" }));
  await screen.findByText("Set up your authenticator");
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the password step", () => {
  it("focuses the email field on mount", async () => {
    renderLogin();
    expect(document.activeElement).toBe(screen.getByLabelText("Operator email"));
  });

  it("shows one generic message for a wrong credential, never the server's own wording", async () => {
    vi.spyOn(authApi, "login").mockRejectedValue(new ApiError(401, "Those credentials are not valid", null));
    renderLogin();
    await userEvent.type(screen.getByLabelText("Operator email"), "b@opsgrids.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in to console" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("That email or password is incorrect.");
  });

  it("goes to the code step on mfa_required", async () => {
    await signInToCode();
    expect(screen.getByLabelText("Code")).toBeTruthy();
  });

  it("goes to enrolment on mfa_enrollment_required", async () => {
    await signInToEnrolCode();
    expect(screen.getByLabelText("Enrolment code")).toBeTruthy();
  });
});

describe("the code step (mfa_required)", () => {
  it("focuses the code field on mount", async () => {
    await signInToCode();
    expect(document.activeElement).toBe(screen.getByLabelText("Code"));
  });

  it("shows a wrong code verbatim and keeps what was typed", async () => {
    await signInToCode();
    vi.spyOn(authApi, "verifyMfa").mockRejectedValue(new ApiError(401, "That code is not valid", null));
    await userEvent.type(screen.getByLabelText("Code"), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("That code is not valid");
    expect(screen.getByLabelText("Code")).toHaveProperty("value", "000000");
  });

  it("shows the 503 re-seal problem verbatim, with the recovery-code hint", async () => {
    await signInToCode();
    vi.spyOn(authApi, "verifyMfa").mockRejectedValue(
      new ApiError(503, "Multi-factor re-seal pending — use a recovery code to sign in.", {
        type: "https://errors.opsgrids.dev/mfa-reseal-pending",
      }),
    );
    await userEvent.type(screen.getByLabelText("Code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Multi-factor re-seal pending — use a recovery code to sign in.");
    expect(alert.textContent).toContain("recovery code");
  });

  it("returns to the password step with the lockout sentence verbatim", async () => {
    await signInToCode();
    vi.spyOn(authApi, "verifyMfa").mockRejectedValue(
      new ApiError(403, "This operator account is temporarily locked after several failed attempts. Try again shortly.", null),
    );
    await userEvent.type(screen.getByLabelText("Code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText("Operator sign-in");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe(
      "This operator account is temporarily locked after several failed attempts. Try again shortly.",
    );
  });

  it("goes back to the password step on demand", async () => {
    await signInToCode();
    await userEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(await screen.findByText("Operator sign-in")).toBeTruthy();
  });

  it("signs in on a correct code", async () => {
    await signInToCode();
    vi.spyOn(authApi, "verifyMfa").mockResolvedValue(TOKEN);
    vi.spyOn(authApi, "me").mockResolvedValue(ME);
    await userEvent.type(screen.getByLabelText("Code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(screen.queryByText("Enter your code")).toBeNull());
  });
});

describe("the enrolment-code step (mfa_enrollment_required)", () => {
  it("focuses the code field on mount", async () => {
    await signInToEnrolCode();
    expect(document.activeElement).toBe(screen.getByLabelText("Enrolment code"));
  });

  it("shows a rejected code verbatim and clears the spent field", async () => {
    await signInToEnrolCode();
    vi.spyOn(authApi, "beginEnrolment").mockRejectedValue(
      new ApiError(401, "That enrolment code is not valid or has expired.", null),
    );
    await userEvent.type(screen.getByLabelText("Enrolment code"), "spent-code");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("That enrolment code is not valid or has expired.");
    expect(screen.getByLabelText("Enrolment code")).toHaveProperty("value", "");
  });

  it("moves to the scan step with the secret and otpauth URI", async () => {
    await signInToEnrolCode();
    vi.spyOn(authApi, "beginEnrolment").mockResolvedValue({
      factor_id: "f1",
      secret: "JBSWY3DPEHPK3PXP", // pragma: allowlist secret -- a well-known example TOTP secret, not a real one
      otpauth_uri: "otpauth://totp/OpsGrids:b@opsgrids.com?secret=JBSWY3DPEHPK3PXP&issuer=OpsGrids",
    });
    await userEvent.type(screen.getByLabelText("Enrolment code"), "a-valid-code");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Scan this with your authenticator")).toBeTruthy();
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Authenticator setup code" })).toBeTruthy();
  });
});

describe("the scan step", () => {
  async function toScanStep() {
    await signInToEnrolCode();
    vi.spyOn(authApi, "beginEnrolment").mockResolvedValue({
      factor_id: "f1",
      secret: "JBSWY3DPEHPK3PXP", // pragma: allowlist secret -- a well-known example TOTP secret, not a real one
      otpauth_uri: "otpauth://totp/OpsGrids:b@opsgrids.com?secret=JBSWY3DPEHPK3PXP&issuer=OpsGrids",
    });
    await userEvent.type(screen.getByLabelText("Enrolment code"), "a-valid-code");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText("Scan this with your authenticator");
  }

  it("focuses the 6-digit code field on mount", async () => {
    await toScanStep();
    expect(document.activeElement).toBe(screen.getByLabelText("6-digit code"));
  });

  it("shows the server's message verbatim for a wrong confirmation code", async () => {
    await toScanStep();
    vi.spyOn(authApi, "confirmEnrolment").mockRejectedValue(
      new ApiError(401, "That code is not valid. Check your device's clock.", null),
    );
    await userEvent.type(screen.getByLabelText("6-digit code"), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Confirm and sign in" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("That code is not valid. Check your device's clock.");
  });

  it("shows the recovery codes once confirmed", async () => {
    await toScanStep();
    vi.spyOn(authApi, "confirmEnrolment").mockResolvedValue({
      status: "complete",
      access_token: "tok",
      token_type: "Bearer",
      expires_in: 900,
      operator_roles: ["owner"],
      recovery_codes: ["ab12-cd34-ef56", "11aa-22bb-33cc"],
    });
    await userEvent.type(screen.getByLabelText("6-digit code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Confirm and sign in" }));
    expect(await screen.findByText("Save your recovery codes")).toBeTruthy();
    expect(screen.getByText("ab12-cd34-ef56")).toBeTruthy();
    expect(screen.getByText("11aa-22bb-33cc")).toBeTruthy();
  });
});

describe("the recovery-codes step", () => {
  async function toRecoveryStep() {
    await signInToEnrolCode();
    vi.spyOn(authApi, "beginEnrolment").mockResolvedValue({
      factor_id: "f1",
      secret: "JBSWY3DPEHPK3PXP", // pragma: allowlist secret -- a well-known example TOTP secret, not a real one
      otpauth_uri: "otpauth://totp/OpsGrids:b@opsgrids.com?secret=JBSWY3DPEHPK3PXP",
    });
    await userEvent.type(screen.getByLabelText("Enrolment code"), "a-valid-code");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText("Scan this with your authenticator");
    vi.spyOn(authApi, "confirmEnrolment").mockResolvedValue({
      status: "complete",
      access_token: "tok",
      token_type: "Bearer",
      expires_in: 900,
      operator_roles: ["owner"],
      recovery_codes: ["ab12-cd34-ef56"],
    });
    await userEvent.type(screen.getByLabelText("6-digit code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Confirm and sign in" }));
    await screen.findByText("Save your recovery codes");
  }

  it("focuses the heading on mount", async () => {
    await toRecoveryStep();
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Save your recovery codes" }));
  });

  it("keeps Continue disabled until the operator confirms they saved the codes", async () => {
    await toRecoveryStep();
    const button = screen.getByRole("button", { name: "Continue to the console" });
    expect(button).toHaveProperty("disabled", true);
    await userEvent.click(screen.getByRole("checkbox", { name: "I've saved these codes" }));
    expect(button).toHaveProperty("disabled", false);
  });

  it("signs in once the codes are saved and confirmed", async () => {
    await toRecoveryStep();
    vi.spyOn(authApi, "me").mockResolvedValue(ME);
    await userEvent.click(screen.getByRole("checkbox", { name: "I've saved these codes" }));
    await userEvent.click(screen.getByRole("button", { name: "Continue to the console" }));
    await waitFor(() => expect(screen.queryByText("Save your recovery codes")).toBeNull());
  });
});
