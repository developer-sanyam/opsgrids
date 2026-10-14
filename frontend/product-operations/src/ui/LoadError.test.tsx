import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import { LoadError } from "./LoadError";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("LoadError", () => {
  it("offers a way back, not a retry, when access is denied", () => {
    const back = vi.spyOn(window.history, "back").mockImplementation(() => undefined);
    render(<LoadError error={new ApiError(403, "Forbidden", null)} noun="goods receipts" onRetry={vi.fn()} />);
    expect(screen.getByText("You don't have access to goods receipts")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Go back" }));
    expect(back).toHaveBeenCalledOnce();
  });

  it("says what happened instead of the transport's own sentence when the server fails without a reason", () => {
    render(<LoadError error={new ApiError(500, "Request failed with 500", null)} noun="goods receipts" onRetry={vi.fn()} />);
    expect(screen.queryByText(/Request failed with 500/)).toBeNull();
    expect(screen.getByText(/The server had a problem loading goods receipts/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
