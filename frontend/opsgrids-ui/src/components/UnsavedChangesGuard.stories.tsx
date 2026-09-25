import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, screen, waitFor } from "storybook/test";
import { Link, RouterProvider, createMemoryRouter } from "react-router-dom";

import { Card } from "./Card";
import { TextField } from "./TextField";
import { UnsavedChangesGuard } from "./UnsavedChangesGuard";

/** A full-page form with its own data router — the guard needs one, as every app has. */
function EditPurchaseOrder() {
  const [supplier, setSupplier] = useState("");
  return (
    <Card className="flex max-w-lg flex-col gap-4 p-5">
      <h1 className="text-lg font-bold text-ink">New purchase order</h1>
      <TextField label="Supplier" value={supplier} onChange={(event) => setSupplier(event.target.value)} />
      <Link to="/orders" className="self-start text-sm font-semibold text-accent-ink underline">
        Back to purchase orders
      </Link>
      <UnsavedChangesGuard when={supplier !== ""} subject="this purchase order" />
    </Card>
  );
}

function App() {
  const [router] = useState(() =>
    createMemoryRouter(
      [
        { path: "/orders/new", element: <EditPurchaseOrder /> },
        { path: "/orders", element: <p className="text-sm text-ink">Purchase orders</p> },
      ],
      { initialEntries: ["/orders/new"] },
    ),
  );
  return <RouterProvider router={router} />;
}

const meta = {
  title: "Overlays/UnsavedChangesGuard",
  component: UnsavedChangesGuard,
  args: { when: true },
  render: () => <App />,
} satisfies Meta<typeof UnsavedChangesGuard>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing typed: the link simply navigates. */
export const Clean: Story = {};

/** Typed, then a link: the route change is held and asked about; Stay is focused. */
export const LeavingWithChanges: Story = {
  tags: ["viewport"],
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByLabelText("Supplier"), "Kaveri Textiles");
    await userEvent.click(canvas.getByRole("link", { name: "Back to purchase orders" }));
    // The question is portalled to <body>, outside the story's canvas; it is ready once it has focus.
    const question = await screen.findByRole("dialog", { name: "Leave without saving?" });
    await waitFor(() => expect(question.contains(document.activeElement)).toBe(true));
  },
};
