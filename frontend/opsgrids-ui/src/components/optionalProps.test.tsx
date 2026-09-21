/**
 * The optional props, as a caller sees them through `exactOptionalPropertyTypes`.
 *
 * Two rules are pinned here, and both were learned the expensive way — by breaking nine apps at
 * once:
 *
 * 1. **An optional prop must accept an explicit `undefined`.** `error={a ?? b}` is
 *    `string | undefined`, and under `exactOptionalPropertyTypes` a prop declared `error?: string`
 *    rejects it. Four apps had written their own `maybe(key, value)` spread helper before the
 *    props were declared `?: T | undefined`.
 * 2. **A function-typed optional prop must be parenthesised.** `onRowClick?: (row: T) => void |
 *    undefined` parses as a function *returning* `void | undefined`, and a union return type
 *    stops accepting the `void | Promise<void>` that react-router's `navigate` returns. It has to
 *    be `((row: T) => void) | undefined`.
 *
 * Both are compile-time facts, so this file failing to typecheck **is** the failure — the runtime
 * assertions below only keep the suite honest about having run it.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Combobox } from "./Combobox";
import { ConfirmDialog } from "./ConfirmDialog";
import { DataTable } from "./DataTable";
import { FormDialog } from "./FormDialog";
import { InlineAlert } from "./InlineAlert";
import { KpiCard } from "./KpiCard";
import { Pagination } from "./Pagination";
import { RadioGroup } from "./RadioGroup";
import { CheckboxGroup } from "./CheckboxGroup";
import { RowActions } from "./RowActions";
import { Select } from "./Select";
import { ErrorState } from "./States";
import { TextArea } from "./TextArea";
import { TextField } from "./TextField";
import { Timeline } from "./Timeline";

interface Row {
  id: string;
  name: string;
}

const ROWS: Row[] = [{ id: "r-1", name: "Acme Ltd" }];

/**
 * The shape react-router's `navigate` has: it may return a promise. Every callback prop in the
 * system has to accept it, because that is what a screen passes when a row click routes.
 */
function navigate(to: string): void | Promise<void> {
  return to === "" ? Promise.resolve() : undefined;
}

describe("optional props accept an explicit undefined", () => {
  it("on every FormField-based control", () => {
    // Exactly what a screen holds: a value that may or may not be there.
    const problem: string | undefined = undefined;
    const helper: string | undefined = undefined;

    render(
      <>
        <TextField label="Name" readOnly value="" error={problem} helper={helper} />
        <TextArea label="Note" readOnly value="" error={problem} helper={helper} />
        <Select
          label="Status"
          value=""
          onChange={() => undefined}
          options={[{ value: "", label: "Any" }]}
          error={problem}
          helper={helper}
        />
        <Combobox
          label="Owner"
          value={null}
          onChange={() => undefined}
          loadOptions={() => Promise.resolve([])}
          error={problem}
          helper={helper}
        />
        <RadioGroup
          legend="Change to"
          name="change-to"
          value={null}
          onChange={() => undefined}
          options={[{ value: "a", label: "A" }]}
          error={problem}
          helper={helper}
        />
        <CheckboxGroup
          label="Roles"
          value={[]}
          onChange={() => undefined}
          options={[{ value: "a", label: "A" }]}
          error={problem}
          helper={helper}
        />
      </>,
    );

    expect(screen.getByLabelText("Name")).toBeDefined();
  });

  it("on the dialogs, the alerts and the error state", () => {
    const message: string | undefined = undefined;
    const reference: string | undefined = undefined;

    render(
      <>
        <ConfirmDialog
          open={false}
          onClose={() => undefined}
          onConfirm={() => undefined}
          title="Delete this?"
          body="It goes for good."
          confirmLabel="Delete"
          error={message}
          reference={reference}
        />
        <FormDialog
          open={false}
          onClose={() => undefined}
          onSubmit={() => undefined}
          title="New widget"
          submitLabel="Create"
          error={message}
          reference={reference}
        >
          <p>Fields</p>
        </FormDialog>
        <InlineAlert tone="danger" title="Couldn't save" reference={reference} />
        <ErrorState message="Couldn't load" reference={reference} onRetry={() => navigate("/")} />
      </>,
    );

    // The inline alert and the error state both announce; the dialogs are closed.
    expect(screen.getAllByRole("alert")).toHaveLength(2);
  });
});

describe("callback props accept a handler that returns a promise", () => {
  it("on the table, the pagination, the row actions and the timeline", () => {
    render(
      <>
        <DataTable
          rows={ROWS}
          rowKey={(row) => row.id}
          columns={[{ key: "name", header: "Name", render: (row) => row.name }]}
          // `navigate` returns `void | Promise<void>` — the exact shape that a bare
          // `(row: T) => void | undefined` declaration rejects.
          onRowClick={(row) => navigate(`/rows/${row.id}`)}
          onSort={(key) => navigate(`/rows?sort=${key}`)}
          isSelected={(row) => row.id === "r-1"}
          selection={{
            selected: new Set<string>(),
            onToggle: () => undefined,
            onToggleAll: () => undefined,
            label: (row) => row.name,
          }}
        />
        <Pagination
          page={1}
          pages={2}
          total={30}
          size={25}
          noun="rows"
          onPageChange={(page) => navigate(`/rows?page=${page}`)}
          onSizeChange={(size) => navigate(`/rows?size=${size}`)}
        />
        <RowActions
          label="Acme Ltd"
          actions={[{ label: "Edit", onSelect: () => navigate("/rows/r-1/edit") }]}
        />
        <Timeline entries={[]} expandLabel={(total) => `Show all ${total}`} />
      </>,
    );

    expect(screen.getByRole("table")).toBeDefined();
  });
});

describe("a KPI with no history", () => {
  it("renders without a delta or a sparkline when neither is known", () => {
    render(<KpiCard label="Open incidents" value="12" icon={<span />} tone="info" caption="Right now" />);

    expect(screen.getByText("12")).toBeDefined();
    expect(screen.getByText("Open incidents")).toBeDefined();
    expect(screen.getByText("Right now")).toBeDefined();
    // No delta pill, and no sparkline: the card has nothing to compare against and says so by
    // showing nothing rather than by inventing a trend.
    expect(document.querySelector("svg")).toBeNull();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("still renders both when they are known", () => {
    render(
      <KpiCard
        label="Revenue"
        value="₹70.38L"
        deltaPct={4.2}
        series={[1, 4, 3, 6]}
        icon={<span />}
        tone="accent"
      />,
    );

    expect(screen.getByText(/4\.2/)).toBeDefined();
    expect(document.querySelector("svg")).not.toBeNull();
  });
});
