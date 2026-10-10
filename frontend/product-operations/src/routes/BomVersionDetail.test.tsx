/**
 * The version screen: a draft offers edit and publish controls only to those holding the
 * permissions; a published version is read-only and says why; and a BOM that would contain itself
 * is refused with the server's sentence shown in the dialog.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { problem, route, stubApi } from "@/test-utils/stubApi";
import { bom, component, version } from "@/test-utils/manufacturingFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { BomVersionDetailPage } from "./BomVersionDetail";

const READ = "operations.manufacturing.bom.read:ORG";
const WRITE = "operations.manufacturing.bom.write:ORG";
const PUBLISH = "operations.manufacturing.bom.publish:ORG";

const ROUTE = { path: "/manufacturing/boms/:bomId/versions/:versionId", at: "/manufacturing/boms/bom-1/versions/ver-2" };

afterEach(() => {
  vi.unstubAllGlobals();
});

function serve(v = version()) {
  return stubApi([
    route("GET", /\/boms\/bom-1$/, { body: { ...bom(), versions: [] } }),
    route("GET", /\/versions\/ver-2$/, { body: v }),
  ]);
}

describe("BomVersionDetailPage", () => {
  it("shows the batch size in the item's unit and the components with their substitutes", async () => {
    serve(
      version({
        components: [
          component({
            is_produced: true,
            substitutes: [{ id: "sub-1", substitute_item_id: "item-poly", sku: "POLY", name: "Poly blend", uom: "m", ratio: "1.25", priority: 1 }],
          }),
        ],
      }),
    );
    renderScreen(<BomVersionDetailPage />, [READ], ROUTE);

    expect(await screen.findByText(/Per 10 pc of SHIRT/)).toBeDefined();
    expect(screen.getByText("FABRIC")).toBeDefined();
    expect(screen.getByText("1.5 m")).toBeDefined();
    expect(screen.getByText("2.5%")).toBeDefined();
    expect(screen.getByText("Produced")).toBeDefined();
    expect(screen.getByText("POLY")).toBeDefined();
    expect(screen.getByText("ratio 1.25")).toBeDefined();
  });

  it("shows the server's sentence when the version cannot be loaded", async () => {
    stubApi([
      route("GET", /\/boms\/bom-1$/, { body: { ...bom(), versions: [] } }),
      route("GET", /\/versions\/ver-2$/, problem(404, "That version is not part of this BOM")),
    ]);
    renderScreen(<BomVersionDetailPage />, [READ], ROUTE);
    expect(await screen.findByText("That version is not part of this BOM")).toBeDefined();
  });

  it("offers a draft's edit controls to a writer, and publishing only to a publisher", async () => {
    serve();
    renderScreen(<BomVersionDetailPage />, [READ, WRITE], ROUTE);
    await screen.findByText("FABRIC");
    expect(screen.getByRole("button", { name: "Edit details" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Add component" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Discard draft" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
  });

  it("offers Publish to a publisher who cannot edit", async () => {
    serve();
    renderScreen(<BomVersionDetailPage />, [READ, PUBLISH], ROUTE);
    await screen.findByText("FABRIC");
    expect(screen.getByRole("button", { name: "Publish" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard draft" })).toBeNull();
  });

  it("offers a writer row actions on each draft component", async () => {
    serve();
    renderScreen(<BomVersionDetailPage />, [READ, WRITE], ROUTE);
    await screen.findByText("FABRIC");
    expect(screen.getByRole("button", { name: "Edit Component FABRIC" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Add substitute Component FABRIC" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Remove Component FABRIC" })).toBeDefined();
  });

  it("offers a draft no controls to a reader", async () => {
    serve();
    renderScreen(<BomVersionDetailPage />, [READ], ROUTE);
    await screen.findByText("FABRIC");
    for (const name of ["Edit details", "Add component", "Discard draft", "Publish", "Retire"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.queryByRole("button", { name: "Edit Component FABRIC" })).toBeNull();
  });

  it("explains that a published version is immutable, shows its hash, and offers no edit even to a writer", async () => {
    serve(version({ status: "PUBLISHED", published_at: "2026-09-10T08:00:00Z", content_hash: "abc123def456" })); // pragma: allowlist secret — a fixture hash
    renderScreen(<BomVersionDetailPage />, [READ, WRITE, PUBLISH], ROUTE);

    expect(await screen.findByText(/immutable — start a new version to change it/i)).toBeDefined();
    expect(screen.getByText("abc123def456")).toBeDefined(); // pragma: allowlist secret — the fixture hash
    for (const name of ["Edit details", "Add component", "Discard draft", "Publish"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.getByRole("button", { name: "Retire" })).toBeDefined();
  });

  it("publishes after a confirmation that says it becomes immutable", async () => {
    const api = stubApi([
      route("GET", /\/boms\/bom-1$/, { body: { ...bom(), versions: [] } }),
      route("GET", /\/versions\/ver-2$/, { body: version() }),
      route("POST", /\/versions\/ver-2\/publish$/, { body: version({ status: "PUBLISHED", content_hash: "h1" }) }),
    ]);
    const user = userEvent.setup();
    renderScreen(<BomVersionDetailPage />, [READ, PUBLISH], ROUTE);

    await user.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/immutable/)).toBeDefined();
    await user.click(within(dialog).getByRole("button", { name: "Publish" }));

    await waitFor(() => expect(api.calls("POST", /publish$/).length).toBe(1));
    expect(api.calls("POST", /publish$/)[0]?.headers["Idempotency-Key"]).toBeTruthy();
    expect(await screen.findByText(/immutable — start a new version to change it/i)).toBeDefined();
  });

  it("shows the server's cycle refusal verbatim when a component would make the BOM contain itself", async () => {
    stubApi([
      route("GET", /\/boms\/bom-1$/, { body: { ...bom(), versions: [] } }),
      route("GET", /\/versions\/ver-2$/, { body: version() }),
      route("GET", /\/operations\/inventory$/, { body: { items: [{ id: "item-shirt", sku: "SHIRT", name: "Shirt" }], total: 1, page: 1, size: 20, pages: 1 } }),
      route("POST", /\/components$/, problem(409, "SHIRT would contain itself: SHIRT -> FABRIC -> SHIRT")),
    ]);
    const user = userEvent.setup();
    renderScreen(<BomVersionDetailPage />, [READ, WRITE], ROUTE);

    await user.click(await screen.findByRole("button", { name: "Add component" }));
    await user.type(screen.getByRole("combobox", { name: "Component item" }), "shirt");
    await user.click(await screen.findByRole("option", { name: /SHIRT/ }));
    await user.type(screen.getByLabelText(/^Quantity/), "2");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add component" }));

    expect(await screen.findByText("SHIRT would contain itself: SHIRT -> FABRIC -> SHIRT")).toBeDefined();
  });

  it("explodes a quantity into a tree and the purchased totals", async () => {
    const api = stubApi([
      route("GET", /\/boms\/bom-1$/, { body: { ...bom(), versions: [] } }),
      route("GET", /\/versions\/ver-2$/, { body: version() }),
      route("GET", /\/explosion$/, {
        body: {
          bom_id: "bom-1",
          version_id: "ver-2",
          quantity: "20",
          root: {
            item_id: "item-shirt", sku: "SHIRT", name: "Shirt", uom: "pc", level: 0, quantity: "20", is_produced: true,
            children: [{ item_id: "item-fabric", sku: "FABRIC", name: "Cotton fabric", uom: "m", level: 1, quantity: "30.75", is_produced: false, children: [] }],
          },
          leaf_totals: [{ item_id: "item-fabric", sku: "FABRIC", name: "Cotton fabric", uom: "m", quantity: "30.75" }],
        },
      }),
    ]);
    const user = userEvent.setup();
    renderScreen(<BomVersionDetailPage />, [READ], ROUTE);

    await user.click(await screen.findByRole("tab", { name: "Explosion" }));
    const quantity = screen.getByLabelText(/^Quantity to produce/);
    await user.clear(quantity);
    await user.type(quantity, "20");
    await user.click(screen.getByRole("button", { name: "Explode" }));

    expect(await screen.findByText("Purchased items needed")).toBeDefined();
    expect(api.calls("GET", /explosion$/)[0]?.query.get("quantity")).toBe("20");
    expect(screen.getAllByText("30.75 m").length).toBeGreaterThan(0);
  });
});
