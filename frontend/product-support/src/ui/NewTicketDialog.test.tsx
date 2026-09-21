import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NewTicketDialog } from "./NewTicketDialog";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(handler: (url: string, init: RequestInit) => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => handler(String(input), init)),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Subject"), "Checkout is broken");
  await user.type(screen.getByLabelText("Requester name"), "Helena Vogel");
  await user.type(screen.getByLabelText("Requester email"), "helena@example.com");
  await user.type(screen.getByLabelText("Message"), "Everything is on fire.");
}

describe("NewTicketDialog", () => {
  it("blocks submit with our own message when the subject is empty, and focuses it", async () => {
    stubFetch((url) =>
      url.includes("/support/agents") ? jsonResponse(200, []) : jsonResponse(201, { id: "TK-4099" }),
    );
    const user = userEvent.setup();
    render(<NewTicketDialog onClose={vi.fn()} onCreated={vi.fn()} />);

    await user.type(screen.getByLabelText("Requester name"), "Helena Vogel");
    await user.type(screen.getByLabelText("Requester email"), "helena@example.com");
    await user.type(screen.getByLabelText("Message"), "Everything is on fire.");
    await user.click(screen.getByRole("button", { name: "Create ticket" }));

    expect(await screen.findByText("Give the ticket a subject, up to 300 characters.")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByLabelText("Subject"));
  });

  it("blocks submit on an invalid email before ever calling the server", async () => {
    stubFetch((url) => (url.includes("/support/agents") ? jsonResponse(200, []) : jsonResponse(201, {})));
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(<NewTicketDialog onClose={vi.fn()} onCreated={onCreated} />);

    await user.type(screen.getByLabelText("Subject"), "Checkout is broken");
    await user.type(screen.getByLabelText("Requester name"), "Helena Vogel");
    await user.type(screen.getByLabelText("Requester email"), "not-an-email");
    await user.type(screen.getByLabelText("Message"), "Everything is on fire.");
    await user.click(screen.getByRole("button", { name: "Create ticket" }));

    expect(await screen.findByText("Enter a valid email address.")).toBeTruthy();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("disables the submit button and blocks a second submit while the write is in flight", async () => {
    let resolveCreate: (value: Response) => void = () => undefined;
    stubFetch((url) => {
      if (url.includes("/support/agents")) return jsonResponse(200, []);
      return new Promise<Response>((resolve) => {
        resolveCreate = resolve;
      }) as unknown as Response;
    });
    const user = userEvent.setup();
    render(<NewTicketDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequiredFields(user);

    const submit = screen.getByRole("button", { name: "Create ticket" });
    await user.click(submit);
    await waitFor(() => expect(submit.getAttribute("aria-busy")).toBe("true"));
    await user.click(submit);

    resolveCreate(jsonResponse(201, { id: "TK-4099" }));
  });

  it("on success, closes the dialog and hands the created ticket back to the screen", async () => {
    stubFetch((url) =>
      url.includes("/support/agents") ? jsonResponse(200, []) : jsonResponse(201, { id: "TK-4099" }),
    );
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onCreated = vi.fn();
    render(<NewTicketDialog onClose={onClose} onCreated={onCreated} />);
    await fillRequiredFields(user);

    await user.click(screen.getByRole("button", { name: "Create ticket" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onCreated).toHaveBeenCalledWith({ id: "TK-4099" });
  });

  it("maps a 422 field error onto its control, and keeps the typed values", async () => {
    stubFetch((url) => {
      if (url.includes("/support/agents")) return jsonResponse(200, []);
      return jsonResponse(422, {
        title: "Unprocessable Entity",
        status: 422,
        detail: "Validation failed",
        errors: [{ field: "requester.email", code: "value_error", message: "Enter a real email address." }],
      });
    });
    const user = userEvent.setup();
    render(<NewTicketDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequiredFields(user);

    await user.click(screen.getByRole("button", { name: "Create ticket" }));

    expect(await screen.findByText("Enter a real email address.")).toBeTruthy();
    expect(screen.getByLabelText("Subject")).toHaveProperty("value", "Checkout is broken");
  });

  it("shows a 403 as the form-level failure, not on a field", async () => {
    stubFetch((url) => {
      if (url.includes("/support/agents")) return jsonResponse(200, []);
      return jsonResponse(403, { title: "Forbidden", status: 403, detail: "You cannot open tickets." });
    });
    const user = userEvent.setup();
    render(<NewTicketDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequiredFields(user);

    await user.click(screen.getByRole("button", { name: "Create ticket" }));

    expect(await screen.findByText("You cannot open tickets.")).toBeTruthy();
  });
});
