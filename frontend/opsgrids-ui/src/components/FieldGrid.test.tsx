/**
 * The grid itself is CSS (subgrid), which jsdom cannot lay out. What can be pinned is the
 * contract the CSS relies on: every field is one element with three children at most — label
 * row, control, messages — and carries the `data-field` hook the grid's selectors target.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { FieldGrid } from "./FieldGrid";
import { TextField } from "./TextField";

describe("FieldGrid", () => {
  it("marks each field, and keeps helper and error in one messages element", () => {
    render(
      <FieldGrid className="sm:grid-cols-2">
        <TextField label="Quantity" helper="In kg." error="Required." />
        <TextField label="Note" />
      </FieldGrid>,
    );
    const quantity = screen.getByLabelText("Quantity").closest("[data-field]");
    const note = screen.getByLabelText("Note").closest("[data-field]");
    expect(quantity).not.toBeNull();
    expect(note).not.toBeNull();
    // label row, control, one messages element holding both paragraphs
    expect(quantity?.children).toHaveLength(3);
    expect(quantity?.lastElementChild?.children).toHaveLength(2);
    expect(note?.children).toHaveLength(2);
  });

  it("is a single column until the caller says otherwise", () => {
    const { container } = render(
      <FieldGrid>
        <TextField label="Name" />
      </FieldGrid>,
    );
    expect(container.firstElementChild?.className).toContain("grid-cols-1");
  });
});
