/**
 * How the four list screens hand themselves to the Import/Export button: which entity, which
 * filters (the ones the list is loaded with, held steady between renders), and whether the caller
 * is offered Import. The hook and the button are replaced with recorders: the engine and the dialog
 * are not under test here, only what the screens give them.
 */
import type { ReactElement } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionProvider } from "@opsgrids/client";
import type { UseImportExportResult } from "@opsgrids/client";
import type { ImportExportButtonProps } from "@opsgrids/ui";

import { DirectoryContext, EMPTY_DIRECTORY } from "@/data/directoryContext";
import { CompaniesPage } from "@/routes/Companies";
import { ContactsPage } from "@/routes/Contacts";
import { DealsPage } from "@/routes/Deals";
import { LeadsPage } from "@/routes/Leads";
import { pageOf, stubGateway } from "./entityTestKit";

const recorded = vi.hoisted(() => ({
  hook: [] as { entityId: string; filters: unknown }[],
  buttons: [] as unknown[],
}));

const RESULT: UseImportExportResult = {
  state: {
    phase: "menu",
    error: null,
    preview: null,
    progress: null,
    summary: null,
    downloadingTemplate: false,
    exporting: null,
    notice: null,
  },
  actions: {
    openFile: async () => undefined,
    startImport: async () => undefined,
    cancelImport: () => undefined,
    reset: () => undefined,
    downloadTemplate: async () => undefined,
    exportAll: async () => undefined,
    cancelExport: () => undefined,
    downloadReport: async () => undefined,
  },
  meta: {
    noun: "records",
    nounSingular: "record",
    maxRows: 1000,
    exportMaxRows: 10_000,
    requiredHeaders: [],
    canExport: true,
  },
};

vi.mock("@opsgrids/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@opsgrids/client")>();
  return {
    ...actual,
    useImportExport: (entity: { id: string }, options?: { filters?: unknown }) => {
      recorded.hook.push({ entityId: entity.id, filters: options?.filters });
      return RESULT;
    },
  };
});

vi.mock("@opsgrids/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@opsgrids/ui")>();
  return {
    ...actual,
    ImportExportButton: (props: unknown) => {
      recorded.buttons.push(props);
      return <button type="button">Import / Export</button>;
    },
  };
});

beforeEach(() => {
  recorded.hook.length = 0;
  recorded.buttons.length = 0;
  stubGateway(() => pageOf([]));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastHook() {
  const last = recorded.hook.at(-1);
  if (last === undefined) throw new Error("useImportExport was never called");
  return last;
}

function lastButton(): ImportExportButtonProps {
  const last = recorded.buttons.at(-1);
  if (last === undefined) throw new Error("ImportExportButton was never rendered");
  return last as ImportExportButtonProps;
}

function renderScreen(screenElement: ReactElement, grants: string[]) {
  return render(
    <MemoryRouter>
      <PermissionProvider grants={grants}>
        <DirectoryContext.Provider value={{ ...EMPTY_DIRECTORY, status: "success" }}>
          {screenElement}
        </DirectoryContext.Provider>
      </PermissionProvider>
    </MemoryRouter>,
  );
}

interface Case {
  screen: string;
  element: ReactElement;
  entityId: string;
  emptyTitle: string;
  write: string;
  read: string;
  initialFilters: unknown;
  /** A filter control on the screen, and the filters the list is loaded with after using it. */
  pill: string;
  pillFilters: unknown;
  /** Changes something on the screen that is not a filter. */
  unrelated: (user: ReturnType<typeof userEvent.setup>) => Promise<void>;
}

const CASES: Case[] = [
  {
    screen: "Companies",
    element: <CompaniesPage />,
    entityId: "crm.companies",
    emptyTitle: "No companies yet",
    write: "crm.book.company.write:OWN",
    read: "crm.book.company.read:ORG",
    initialFilters: { search: "", status: "all" },
    pill: "Prospects",
    pillFilters: { search: "", status: "prospect" },
    unrelated: (user) => user.selectOptions(screen.getByLabelText("Sort companies"), "revenue"),
  },
  {
    screen: "Contacts",
    element: <ContactsPage />,
    entityId: "crm.contacts",
    emptyTitle: "No contacts yet",
    write: "crm.book.contact.write:OWN",
    read: "crm.book.contact.read:ORG",
    initialFilters: { search: "", status: "all" },
    pill: "Qualified",
    pillFilters: { search: "", status: "qualified" },
    unrelated: (user) => user.selectOptions(screen.getByLabelText("Sort contacts"), "recent"),
  },
  {
    screen: "Leads",
    element: <LeadsPage />,
    entityId: "crm.leads",
    emptyTitle: "No leads yet",
    write: "crm.lead.lead.write:OWN",
    read: "crm.lead.lead.read:ORG",
    initialFilters: { status: "all" },
    pill: "Working",
    pillFilters: { status: "working" },
    unrelated: async (user) => {
      const [capture] = screen.getAllByRole("button", { name: "Capture lead" });
      if (capture === undefined) throw new Error("no Capture lead button");
      await user.click(capture);
    },
  },
  {
    screen: "Deals",
    element: <DealsPage />,
    entityId: "crm.deals",
    emptyTitle: "No matching deals",
    write: "crm.pipeline.deal.write:OWN",
    read: "crm.pipeline.deal.read:ORG",
    initialFilters: { search: "", stage: "all", ownerId: "all" },
    pill: "Proposal",
    pillFilters: { search: "", stage: "proposal", ownerId: "all" },
    unrelated: (user) => user.click(screen.getByRole("tab", { name: "List" })),
  },
];

describe.each(CASES)("$screen screen", (c) => {
  it("gives the Import/Export button the entity's hook result", async () => {
    renderScreen(c.element, [c.write]);
    await screen.findByText(c.emptyTitle);
    expect(screen.getByRole("button", { name: "Import / Export" })).toBeTruthy();
    expect(lastHook().entityId).toBe(c.entityId);
    expect(lastButton().meta).toBe(RESULT.meta);
    expect(lastButton().state).toBe(RESULT.state);
    expect(lastButton().actions).toBe(RESULT.actions);
  });

  it("offers Import to a caller who may create here, and Export to anyone who can see the list", async () => {
    renderScreen(c.element, [c.write]);
    await screen.findByText(c.emptyTitle);
    expect(lastButton().canImport).toBe(true);
    expect(lastButton().canExport).toBe(true);
  });

  it("does not offer Import to a caller who can only read", async () => {
    renderScreen(c.element, [c.read]);
    await screen.findByText(c.emptyTitle);
    expect(lastButton().canImport).toBe(false);
    expect(lastButton().canExport).toBe(true);
  });

  it("exports the list with the filters the screen is loading it with", async () => {
    const user = userEvent.setup();
    renderScreen(c.element, [c.write]);
    await screen.findByText(c.emptyTitle);
    expect(lastHook().filters).toEqual(c.initialFilters);

    await user.click(screen.getByRole("button", { name: c.pill }));
    await screen.findByText(c.emptyTitle);
    expect(lastHook().filters).toEqual(c.pillFilters);
  });

  it("keeps the same filters object while nothing that filters the list has changed", async () => {
    const user = userEvent.setup();
    renderScreen(c.element, [c.write]);
    await screen.findByText(c.emptyTitle);
    const before = lastHook().filters;

    await c.unrelated(user);
    await screen.findByText(c.emptyTitle);

    expect(recorded.hook.length).toBeGreaterThan(1);
    expect(lastHook().filters).toBe(before);
  });
});
