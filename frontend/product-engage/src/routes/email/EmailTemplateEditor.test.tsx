import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as email from "@/api/email";
import type { EmailTemplateOut } from "@/api/email";
import type { Page } from "@/api/types";
import { EmailTemplateEditorPage } from "./EmailTemplateEditor";

/**
 * The template editor: validation, inserting a variable at the caret in either field, the
 * sandboxed preview, and saving without losing what was typed.
 */

const SHIPPED: EmailTemplateOut = {
  id: "order_shipped",
  channel: "email",
  subject: "Your order {{order_number}} has shipped",
  body: "Hi {{customer_name}}, it is on its way.",
  description: "Sent when a parcel leaves the warehouse",
  placeholders: ["customer_name", "order_number"],
  created_at: "2026-09-01T06:00:00Z",
  updated_at: "2026-09-24T06:00:00Z",
};

function page(items: EmailTemplateOut[]): Page<EmailTemplateOut> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

const WRITE = "notification.delivery.template.write:ORG";

function renderEditor(path: string, grants: string[] = [WRITE]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/email/templates/new" element={<EmailTemplateEditorPage />} />
          <Route path="/email/templates/:id" element={<EmailTemplateEditorPage />} />
          <Route path="/email/templates" element={<p>Template list page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function mockList(items: EmailTemplateOut[] = [SHIPPED]) {
  return vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page(items));
}

/** Only the message under a field: the same sentence is also spoken by the screen's live region. */
const FIELD_ERROR = { selector: "p[id$='-error']" };

function body(): HTMLTextAreaElement {
  return screen.getByRole("textbox", { name: /Body/ }) as HTMLTextAreaElement;
}
function subject(): HTMLInputElement {
  return screen.getByRole("textbox", { name: /Subject/ }) as HTMLInputElement;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loading and not found", () => {
  it("says it is loading an existing template", () => {
    vi.spyOn(email, "listEmailTemplates").mockReturnValue(new Promise(() => {}));
    renderEditor("/email/templates/order_shipped");
    expect(screen.getByText("Loading template")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: /Subject/ })).toBeNull();
  });

  it("says when the template is gone and links back to the list", async () => {
    mockList();
    renderEditor("/email/templates/nope");
    expect(await screen.findByText("That template doesn't exist")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to templates" }).getAttribute("href")).toBe(
      "/email/templates",
    );
  });

  it("shows the failure with a retry when the template cannot be read", async () => {
    vi.spyOn(email, "listEmailTemplates").mockRejectedValue(new ApiError(503, "Warming up", null));
    renderEditor("/email/templates/order_shipped");
    expect(await screen.findByText("Couldn't load your email templates")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("editing an existing template", () => {
  it("fills the form and keeps the name fixed, because the name is the identifier", async () => {
    mockList();
    renderEditor("/email/templates/order_shipped");

    expect(await screen.findByRole("heading", { name: "Edit Template" })).toBeTruthy();
    expect(subject().value).toBe(SHIPPED.subject);
    expect(body().value).toBe(SHIPPED.body);
    const name = screen.getByRole("textbox", { name: /Template Name/ }) as HTMLInputElement;
    expect(name.value).toBe("order_shipped");
    expect(name.readOnly).toBe(true);
  });

  it("saves through the service and says so", async () => {
    mockList();
    const save = vi.spyOn(email, "saveEmailTemplate").mockResolvedValue(SHIPPED);
    const user = userEvent.setup();
    renderEditor("/email/templates/order_shipped");

    await screen.findByRole("heading", { name: "Edit Template" });
    await user.click(screen.getByRole("button", { name: "Save Template" }));

    expect(await screen.findByText("Template saved")).toBeTruthy();
    expect(save).toHaveBeenCalledWith({
      code: "order_shipped",
      channel: "email",
      subject: SHIPPED.subject,
      body: SHIPPED.body,
      description: SHIPPED.description,
    });
  });

  it("is read-only, with no Save, for someone who may not write templates", async () => {
    mockList();
    renderEditor("/email/templates/order_shipped", ["notification.delivery.template.read:ORG"]);

    await screen.findByRole("heading", { name: "Edit Template" });
    expect(screen.queryByRole("button", { name: "Save Template" })).toBeNull();
    expect(subject().readOnly).toBe(true);
    expect(screen.getByText("You can read this template but not change it")).toBeTruthy();
  });
});

describe("creating a template", () => {
  it("says what is missing next to each field and saves nothing", async () => {
    mockList([]);
    const save = vi.spyOn(email, "saveEmailTemplate");
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await user.click(await screen.findByRole("button", { name: "Save Template" }));

    expect(screen.getByText("Give the template a name.")).toBeTruthy();
    expect(screen.getByText("Every email needs a subject.")).toBeTruthy();
    expect(screen.getByText("The body can't be empty.")).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it("refuses a name outside the service's grammar before the round trip", async () => {
    mockList([]);
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await user.type(await screen.findByRole("textbox", { name: /Template Name/ }), "Order Shipped");
    await user.click(screen.getByRole("button", { name: "Save Template" }));

    expect(screen.getByText("Use lowercase letters, digits and underscores only.")).toBeTruthy();
  });

  it("refuses a name that is already taken, so a live template is never silently replaced", async () => {
    mockList([SHIPPED]);
    const save = vi.spyOn(email, "saveEmailTemplate");
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    const name = await screen.findByRole("textbox", { name: /Template Name/ });
    await waitFor(() => expect(email.listEmailTemplates).toHaveBeenCalled());
    await user.type(name, "order_shipped");
    await user.type(subject(), "Shipped");
    await user.type(body(), "On its way");
    await user.click(screen.getByRole("button", { name: "Save Template" }));

    const message = /A template named order_shipped already exists/;
    expect(screen.getByText(message, FIELD_ERROR)).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
    // The error is tied to the field, the save is announced as not having happened, and focus
    // lands on the field to fix.
    expect(name.getAttribute("aria-invalid")).toBe("true");
    const described = (name.getAttribute("aria-describedby") ?? "").split(" ");
    expect(described.some((id) => message.test(document.getElementById(id)?.textContent ?? ""))).toBe(true);
    expect(screen.getByText(/^Template not saved\. A template named order_shipped already exists/)).toBeTruthy();
    expect(document.activeElement).toBe(name);
    // What was typed stays.
    expect(subject().value).toBe("Shipped");
    expect(body().value).toBe("On its way");
  });

  it("compares names without regard to case", async () => {
    mockList([{ ...SHIPPED, id: "Welcome_Email" }]);
    const save = vi.spyOn(email, "saveEmailTemplate");
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    const name = await screen.findByRole("textbox", { name: /Template Name/ });
    await waitFor(() => expect(email.listEmailTemplates).toHaveBeenCalled());
    await user.type(name, "welcome_email");
    await user.type(subject(), "Welcome");
    await user.type(body(), "Hello");
    await user.click(screen.getByRole("button", { name: "Save Template" }));

    expect(screen.getByText(/A template named welcome_email already exists/, FIELD_ERROR)).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it("does not save, and asks for the list again, while the existing names are unknown", async () => {
    const list = vi
      .spyOn(email, "listEmailTemplates")
      .mockRejectedValueOnce(new ApiError(503, "Warming up", null))
      .mockResolvedValue(page([SHIPPED]));
    const save = vi.spyOn(email, "saveEmailTemplate");
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    const name = await screen.findByRole("textbox", { name: /Template Name/ });
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
    await user.type(name, "order_shipped");
    await user.type(subject(), "Shipped");
    await user.type(body(), "On its way");
    await user.click(screen.getByRole("button", { name: "Save Template" }));

    expect(await screen.findByText(/Couldn't check which template names are already in use/)).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));

    // Once the names are known the same submit is judged on them.
    await user.click(screen.getByRole("button", { name: "Save Template" }));
    expect(await screen.findByText(/A template named order_shipped already exists/, FIELD_ERROR)).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it("saves, then opens the saved template", async () => {
    const list = mockList([]);
    const save = vi.spyOn(email, "saveEmailTemplate").mockImplementation(async (input) => {
      list.mockResolvedValue(page([{ ...SHIPPED, id: input.code }]));
      return { ...SHIPPED, id: input.code };
    });
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await user.type(await screen.findByRole("textbox", { name: /Template Name/ }), "order_shipped");
    await user.type(subject(), "Shipped");
    await user.type(body(), "On its way");
    await user.click(screen.getByRole("button", { name: "Save Template" }));

    expect(await screen.findByRole("heading", { name: "Edit Template" })).toBeTruthy();
    expect(save).toHaveBeenCalledWith({
      code: "order_shipped",
      channel: "email",
      subject: "Shipped",
      body: "On its way",
    });
  });

  it("keeps what was typed and puts the service's field error on its field", async () => {
    mockList([]);
    vi.spyOn(email, "saveEmailTemplate").mockRejectedValue(
      new ApiError(422, "Invalid template", {
        errors: [{ field: "subject", code: "too_long", message: "That subject is too long." }],
      }),
    );
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await user.type(await screen.findByRole("textbox", { name: /Template Name/ }), "order_shipped");
    await user.type(subject(), "Shipped");
    await user.type(body(), "On its way");
    await user.click(screen.getByRole("button", { name: "Save Template" }));

    expect(await screen.findByText("That subject is too long.")).toBeTruthy();
    expect(subject().value).toBe("Shipped");
    expect(body().value).toBe("On its way");
  });

  it("shows a refusal that names no field as an alert, and keeps the form", async () => {
    mockList([]);
    vi.spyOn(email, "saveEmailTemplate").mockRejectedValue(new ApiError(403, "Not allowed", null));
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await user.type(await screen.findByRole("textbox", { name: /Template Name/ }), "order_shipped");
    await user.type(subject(), "Shipped");
    await user.type(body(), "On its way");
    await user.click(screen.getByRole("button", { name: "Save Template" }));

    expect(await screen.findByText("Couldn't save the template")).toBeTruthy();
    expect(screen.getByText("Not allowed")).toBeTruthy();
    expect(body().value).toBe("On its way");
  });

  it("sends one request however many times Save is pressed", async () => {
    mockList([]);
    const save = vi.spyOn(email, "saveEmailTemplate").mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await user.type(await screen.findByRole("textbox", { name: /Template Name/ }), "order_shipped");
    await user.type(subject(), "Shipped");
    await user.type(body(), "On its way");
    const button = screen.getByRole("button", { name: "Save Template" });
    await user.click(button);
    await user.click(button);

    expect(save).toHaveBeenCalledTimes(1);
    expect(button.getAttribute("aria-busy")).toBe("true");
  });
});

describe("variables", () => {
  it("offers the placeholders this template and the others already use", async () => {
    mockList([SHIPPED, { ...SHIPPED, id: "invoice", placeholders: ["invoice_total"] }]);
    renderEditor("/email/templates/order_shipped");

    await screen.findByRole("heading", { name: "Edit Template" });
    expect(screen.getByRole("button", { name: "Insert {{customer_name}}" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Insert {{order_number}}" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Insert {{invoice_total}}" })).toBeTruthy();
  });

  it("inserts a variable at the caret in the body, and hands focus back to it", async () => {
    mockList();
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await screen.findByRole("textbox", { name: /Template Name/ });
    await user.type(body(), "Hi , welcome");
    body().setSelectionRange(3, 3);
    await user.click(screen.getByRole("button", { name: "Insert {{customer_name}}" }));

    expect(body().value).toBe("Hi {{customer_name}}, welcome");
    expect(document.activeElement).toBe(body());
    expect(body().selectionStart).toBe("Hi {{customer_name}}".length);
  });

  it("inserts into the subject when that is the field last used", async () => {
    mockList();
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await screen.findByRole("textbox", { name: /Template Name/ });
    await user.type(body(), "Body text");
    await user.type(subject(), "Order  shipped");
    subject().setSelectionRange(6, 6);
    await user.click(screen.getByRole("button", { name: "Insert {{order_number}}" }));

    expect(subject().value).toBe("Order {{order_number}} shipped");
    expect(body().value).toBe("Body text");
  });

  it("replaces the selected text", async () => {
    mockList();
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await screen.findByRole("textbox", { name: /Template Name/ });
    await user.type(body(), "Hi NAME!");
    body().setSelectionRange(3, 7);
    await user.click(screen.getByRole("button", { name: "Insert {{customer_name}}" }));

    expect(body().value).toBe("Hi {{customer_name}}!");
  });

  it("adds a new variable by name and inserts it; refuses a name the service would not read", async () => {
    mockList([]);
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await screen.findByRole("textbox", { name: /Template Name/ });
    const name = screen.getByRole("textbox", { name: "New variable name" });
    await user.type(name, "bad name");
    await user.click(screen.getByRole("button", { name: "Add Variable" }));
    expect(screen.getByText("Use letters, digits, underscores and dots, like customer_name.")).toBeTruthy();
    expect(body().value).toBe("");

    await user.clear(name);
    await user.type(name, "delivery_date");
    await user.click(screen.getByRole("button", { name: "Add Variable" }));
    expect(body().value).toBe("{{delivery_date}}");
    expect(screen.getByRole("button", { name: "Insert {{delivery_date}}" })).toBeTruthy();
  });

  it("filters the variables by the search", async () => {
    mockList();
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await screen.findByRole("textbox", { name: /Template Name/ });
    await user.type(screen.getByRole("searchbox", { name: "Search variables" }), "order");

    expect(screen.getByRole("button", { name: "Insert {{order_number}}" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Insert {{customer_name}}" })).toBeNull();
  });
});

describe("preview", () => {
  it("is a sandboxed frame with no permissions at all", async () => {
    mockList([]);
    renderEditor("/email/templates/new");

    const frame = await screen.findByTitle("Email preview");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("sandbox")).toBe("");
  });

  it("shows the typed text escaped, so a script in a body is only text", async () => {
    mockList([]);
    const user = userEvent.setup();
    renderEditor("/email/templates/new");

    await screen.findByTitle("Email preview");
    await user.type(subject(), "Hello");
    fireEvent.change(body(), { target: { value: "<script>alert(1)</script> {{name}}" } });

    await waitFor(() => {
      const doc = screen.getByTitle("Email preview").getAttribute("srcdoc") ?? "";
      expect(doc).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
      expect(doc).toContain("<mark>{{name}}</mark>");
      expect(doc).not.toContain("<script>");
      expect(doc).toContain("Hello");
    });
  });
});

describe("the unsubscribe notice", () => {
  it("says where the unsubscribe link is and is not added", async () => {
    mockList([]);
    renderEditor("/email/templates/new");
    expect(await screen.findByText("Unsubscribe footer")).toBeTruthy();
    expect(screen.getByText(/added automatically to campaign emails only/)).toBeTruthy();
  });
});
