import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HomePage } from "./Home";

describe("HomePage", () => {
  it("names the screen and explains there are no tasks yet, honestly — not a fake list", () => {
    render(<HomePage />);

    expect(screen.getByRole("heading", { name: "Tasks" })).toBeTruthy();
    expect(screen.getByText("No tasks yet")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
