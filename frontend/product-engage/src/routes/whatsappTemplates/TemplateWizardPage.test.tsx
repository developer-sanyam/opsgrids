import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as api from "@/api/whatsappTemplates";
import { routes } from "./index";
import { TemplateWizardPage } from "./TemplateWizardPage";
import { APPROVED, READ_AND_WRITE } from "./test-fixtures";

afterEach(() => {
  vi.restoreAllMocks();
});

const CREATED = { ...APPROVED, id: "order_shipped", status: "pending" as const, meta_template_id: null };

function renderWizard() {
  return render(
    <PermissionProvider grants={READ_AND_WRITE}>
      <MemoryRouter initialEntries={["/whatsapp/templates/new"]}>
        <Routes>
          <Route path="/whatsapp/templates/new" element={<TemplateWizardPage />} />
          <Route path="/whatsapp/templates" element={<p>List screen</p>} />
          <Route path="/whatsapp/templates/:id" element={<p>Detail screen</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

/** Curly braces are user-event's key syntax, so text holding `{{1}}` goes in by change event. */
function setValue(control: HTMLElement, value: string) {
  fireEvent.change(control, { target: { value } });
}

function currentStep(): string {
  const current = document.querySelector('[aria-current="step"]');
  return current?.textContent ?? "";
}

async function fillDetails(user: UserEvent, name = "order_shipped") {
  await user.type(screen.getByLabelText("Template name"), name);
}

async function next(user: UserEvent, label: string) {
  await user.click(screen.getByRole("button", { name: label }));
}

async function fillContent(user: UserEvent) {
  setValue(screen.getByLabelText("Message text"), "Hello {{1}}, your order has shipped.");
  await user.type(screen.getByLabelText("Example for {{1}}"), "Rahul");
}

describe("the steps", () => {
  it("shows four steps, starting on Details, with the mock's copy", () => {
    renderWizard();
    const steps = within(screen.getByRole("navigation", { name: "Progress" })).getAllByRole("listitem");
    expect(steps.map((step) => step.textContent)).toEqual([
      expect.stringContaining("Details"),
      expect.stringContaining("Content"),
      expect.stringContaining("Review"),
      expect.stringContaining("Submit"),
    ]);
    expect(currentStep()).toContain("Details");
    expect(screen.getByRole("heading", { name: "Create WhatsApp template" })).toBeTruthy();
    expect(screen.getByText("Create a new template and submit it for Meta approval.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next: Content" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
  });

  it("walks Details, Content, Review, Submit and back again, keeping what was entered", async () => {
    const user = userEvent.setup();
    renderWizard();

    await fillDetails(user);
    await next(user, "Next: Content");
    expect(currentStep()).toContain("Content");
    await fillContent(user);

    await next(user, "Next: Review");
    expect(currentStep()).toContain("Review");
    expect(screen.getByText("Ready to submit")).toBeTruthy();

    await next(user, "Next: Submit");
    expect(currentStep()).toContain("Submit");
    expect(screen.getByRole("button", { name: "Submit for review" })).toBeTruthy();

    await next(user, "Back");
    await next(user, "Back");
    await next(user, "Back");
    expect(currentStep()).toContain("Details");
    expect((screen.getByLabelText("Template name") as HTMLInputElement).value).toBe("order_shipped");
    await next(user, "Next: Content");
    expect((screen.getByLabelText("Message text") as HTMLTextAreaElement).value).toBe(
      "Hello {{1}}, your order has shipped.",
    );
    expect((screen.getByLabelText("Example for {{1}}") as HTMLInputElement).value).toBe("Rahul");
  });

  it("announces the step it moves to", async () => {
    const user = userEvent.setup();
    renderWizard();
    await fillDetails(user);
    await next(user, "Next: Content");
    await waitFor(() => expect(screen.getAllByText("Step 2 of 4: Content").length).toBeGreaterThan(0));
  });
});

describe("guarded progress", () => {
  it("does not leave Details with a bad name, and says which field", async () => {
    const user = userEvent.setup();
    renderWizard();

    await next(user, "Next: Content");
    expect(currentStep()).toContain("Details");
    expect(screen.getByText("Enter a template name.")).toBeTruthy();
    expect(screen.getByText("Check the fields marked below")).toBeTruthy();
    expect(screen.getByLabelText("Template name").getAttribute("aria-invalid")).toBe("true");

    await user.type(screen.getByLabelText("Template name"), "Not Valid!");
    await next(user, "Next: Content");
    expect(currentStep()).toContain("Details");
    expect(screen.getByText(/Use lowercase letters, numbers and underscores only/)).toBeTruthy();
  });

  it("does not leave Content with broken numbering or missing examples", async () => {
    const user = userEvent.setup();
    renderWizard();
    await fillDetails(user);
    await next(user, "Next: Content");

    await next(user, "Next: Review");
    expect(currentStep()).toContain("Content");
    expect(screen.getByText("Enter the message text.")).toBeTruthy();

    setValue(screen.getByLabelText("Message text"), "Hi {{1}} and {{3}}");
    await next(user, "Next: Review");
    expect(screen.getByText(/Number variables in order with no gaps/)).toBeTruthy();

    setValue(screen.getByLabelText("Message text"), "Hi {{1}} and {{2}}");
    await next(user, "Next: Review");
    expect(currentStep()).toContain("Content");
    expect(screen.getByText("Enter an example for {{1}}.")).toBeTruthy();
    expect(screen.getByText("Enter an example for {{2}}.")).toBeTruthy();
    expect(screen.getByText(/one example for each of the 2 variables/)).toBeTruthy();
  });

  it("offers only the steps already reached in the step list, and checks the ones it skips", async () => {
    const user = userEvent.setup();
    renderWizard();
    const steps = () => screen.getByRole("navigation", { name: "Progress" });
    expect(within(steps()).queryByRole("button", { name: /Content/ })).toBeNull();

    await fillDetails(user);
    await next(user, "Next: Content");
    await fillContent(user);
    await next(user, "Next: Review");
    expect(within(steps()).getByRole("button", { name: /Details/ })).toBeTruthy();

    // Back to Details and break it; the step list must not carry the user past it unchecked.
    await user.click(within(steps()).getByRole("button", { name: /Details/ }));
    await user.clear(screen.getByLabelText("Template name"));
    await user.click(within(steps()).getByRole("button", { name: /Review/ }));

    expect(currentStep()).toContain("Details");
    expect(screen.getByText("Enter a template name.")).toBeTruthy();
  });

  it("marks a step with a problem in the step list, in words", async () => {
    const user = userEvent.setup();
    renderWizard();
    await next(user, "Next: Content");
    expect(within(screen.getByRole("navigation", { name: "Progress" })).getByText(/Has errors/)).toBeTruthy();
  });
});

describe("content", () => {
  it("draws the message as the customer will see it, filling variables from the examples", async () => {
    const user = userEvent.setup();
    renderWizard();
    await fillDetails(user);
    await next(user, "Next: Content");

    const preview = () => within(screen.getByRole("figure", { name: "Message preview" }));
    setValue(screen.getByLabelText("Message text"), "Hello {{1}}, order {{2}} shipped.");
    expect(preview().getByText("Hello {{1}}, order {{2}} shipped.")).toBeTruthy();

    await user.type(screen.getByLabelText("Example for {{1}}"), "Rahul");
    expect(preview().getByText("Hello Rahul, order {{2}} shipped.")).toBeTruthy();
    await user.type(screen.getByLabelText("Example for {{2}}"), "#ORD-2938");
    expect(preview().getByText("Hello Rahul, order #ORD-2938 shipped.")).toBeTruthy();
  });

  it("Add Variable inserts the next number at the caret", async () => {
    const user = userEvent.setup();
    renderWizard();
    await fillDetails(user);
    await next(user, "Next: Content");

    await user.click(screen.getByRole("button", { name: "Add Variable" }));
    expect((screen.getByLabelText("Message text") as HTMLTextAreaElement).value).toBe("{{1}}");
    await user.click(screen.getByRole("button", { name: "Add Variable" }));
    expect((screen.getByLabelText("Message text") as HTMLTextAreaElement).value).toContain("{{2}}");
    expect(screen.getByLabelText("Example for {{2}}")).toBeTruthy();
  });

  it("wraps the selection in formatting marks", async () => {
    const user = userEvent.setup();
    renderWizard();
    await fillDetails(user);
    await next(user, "Next: Content");

    const body = screen.getByLabelText("Message text") as HTMLTextAreaElement;
    setValue(body, "say hello");
    body.setSelectionRange(4, 9);
    await user.click(screen.getByRole("button", { name: "Bold" }));
    expect(body.value).toBe("say *hello*");
  });

  it("adds and removes buttons and shows each one's own errors", async () => {
    const user = userEvent.setup();
    renderWizard();
    await fillDetails(user);
    await next(user, "Next: Content");
    setValue(screen.getByLabelText("Message text"), "Thanks for shopping.");

    await user.click(screen.getByRole("button", { name: "Add button" }));
    await user.selectOptions(screen.getByLabelText("Button 1 type"), "url");
    await user.type(screen.getByLabelText("Button 1 label"), "Track");
    await user.type(screen.getByLabelText("Button 1 link"), "example.com");
    await next(user, "Next: Review");
    expect(screen.getByText("Enter a full link starting with https://.")).toBeTruthy();
    expect(currentStep()).toContain("Content");

    await user.click(screen.getByRole("button", { name: "Remove button 1" }));
    expect(screen.queryByLabelText("Button 1 label")).toBeNull();
    await next(user, "Next: Review");
    expect(currentStep()).toContain("Review");
  });

  it("stops at ten buttons", async () => {
    const user = userEvent.setup();
    renderWizard();
    await fillDetails(user);
    await next(user, "Next: Content");
    for (let i = 0; i < 10; i += 1) await user.click(screen.getByRole("button", { name: "Add button" }));
    expect((screen.getByRole("button", { name: "Add button" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("asks for a header example only once the header has a variable", async () => {
    const user = userEvent.setup();
    renderWizard();
    await fillDetails(user);
    await next(user, "Next: Content");

    await user.selectOptions(screen.getByLabelText("Header type"), "text");
    setValue(screen.getByLabelText("Header text"), "Order update");
    expect(screen.queryByLabelText(/Example for \{\{1\}\} in the header/)).toBeNull();
    setValue(screen.getByLabelText("Header text"), "Order {{1}}");
    expect(screen.getByLabelText(/Example for \{\{1\}\} in the header/)).toBeTruthy();
  });
});

describe("submitting", () => {
  async function toSubmit(user: UserEvent) {
    await fillDetails(user);
    await next(user, "Next: Content");
    await fillContent(user);
    await next(user, "Next: Review");
    await next(user, "Next: Submit");
  }

  it("submits the request the draft describes and shows the row as pending, not approved", async () => {
    const create = vi.spyOn(api, "createTemplate").mockResolvedValue(CREATED);
    const user = userEvent.setup();
    renderWizard();
    await toSubmit(user);

    await user.click(screen.getByRole("button", { name: "Submit for review" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]?.[0]).toEqual({
      name: "order_shipped",
      category: "utility",
      language: "en_US",
      components: { body: { text: "Hello {{1}}, your order has shipped.", examples: ["Rahul"] } },
    });
    expect(await screen.findByText("Template submitted")).toBeTruthy();
    expect(screen.getByText("Pending Meta review")).toBeTruthy();
    expect(screen.queryByText("Approved")).toBeNull();

    await user.click(screen.getByRole("button", { name: "View template" }));
    expect(await screen.findByText("Detail screen")).toBeTruthy();
  });

  it("cannot be submitted twice while the first is in flight", async () => {
    let finish: (row: typeof CREATED) => void = () => {};
    const create = vi.spyOn(api, "createTemplate").mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const user = userEvent.setup();
    renderWizard();
    await toSubmit(user);

    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    const busy = await screen.findByRole("button", { name: "Submitting…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(busy);
    fireEvent.submit(busy.closest("form") as HTMLFormElement);
    expect(create).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(true);

    finish(CREATED);
    expect(await screen.findByText("Template submitted")).toBeTruthy();
  });

  it("puts a 422 on the field it names, on the step that owns it, and keeps everything entered", async () => {
    const create = vi.spyOn(api, "createTemplate").mockRejectedValue(
      new ApiError(422, "Validation failed", {
        errors: [
          {
            field: "components.body.examples",
            code: "examples_mismatch",
            message: "The examples do not match the variables in the message.",
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderWizard();
    await toSubmit(user);

    await user.click(screen.getByRole("button", { name: "Submit for review" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("That template wasn't submitted")).toBeTruthy();
    expect(currentStep()).toContain("Content");
    expect(screen.getByText("The examples do not match the variables in the message.")).toBeTruthy();
    expect((screen.getByLabelText("Message text") as HTMLTextAreaElement).value).toBe(
      "Hello {{1}}, your order has shipped.",
    );
    expect((screen.getByLabelText("Example for {{1}}") as HTMLInputElement).value).toBe("Rahul");
    expect(within(screen.getByRole("navigation", { name: "Progress" })).getByText(/Has errors/)).toBeTruthy();
  });

  it("puts a 422 about the name under the name field, and clears it once the name is edited", async () => {
    vi.spyOn(api, "createTemplate").mockRejectedValue(
      new ApiError(422, "Validation failed", {
        errors: [
          { field: "name", code: "duplicate", message: "A template named order_shipped already exists." },
        ],
      }),
    );
    const user = userEvent.setup();
    renderWizard();
    await toSubmit(user);
    await user.click(screen.getByRole("button", { name: "Submit for review" }));

    expect(await screen.findByText("A template named order_shipped already exists.")).toBeTruthy();
    expect(currentStep()).toContain("Details");
    expect(screen.getByLabelText("Template name").getAttribute("aria-invalid")).toBe("true");
    expect((screen.getByLabelText("Template name") as HTMLInputElement).value).toBe("order_shipped");

    await user.type(screen.getByLabelText("Template name"), "_v2");
    expect(screen.queryByText("A template named order_shipped already exists.")).toBeNull();
  });

  it("stays on Submit and says so for a refusal that names no field", async () => {
    vi.spyOn(api, "createTemplate").mockRejectedValue(
      new ApiError(409, "WhatsApp is not connected for this organization.", null),
    );
    const user = userEvent.setup();
    renderWizard();
    await toSubmit(user);
    await user.click(screen.getByRole("button", { name: "Submit for review" }));

    expect(await screen.findByText("That template wasn't submitted")).toBeTruthy();
    expect(screen.getByText("WhatsApp is not connected for this organization.")).toBeTruthy();
    expect(screen.getByText("Nothing you entered was lost.")).toBeTruthy();
    expect(currentStep()).toContain("Submit");
    expect((screen.getByRole("button", { name: "Submit for review" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("can be tried again after a failure", async () => {
    const create = vi
      .spyOn(api, "createTemplate")
      .mockRejectedValueOnce(new ApiError(502, "Meta did not answer.", null))
      .mockResolvedValue(CREATED);
    const user = userEvent.setup();
    renderWizard();
    await toSubmit(user);

    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    await screen.findByText("Meta did not answer.");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));
    expect(await screen.findByText("Template submitted")).toBeTruthy();
    expect(create).toHaveBeenCalledTimes(2);
  });

  it("cancels back to the list", async () => {
    const user = userEvent.setup();
    renderWizard();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("List screen")).toBeTruthy();
  });
});

describe("routes", () => {
  it("declares the three template paths, with the create flow before the id", () => {
    expect(routes.map((route) => route.path)).toEqual([
      "/whatsapp/templates",
      "/whatsapp/templates/new",
      "/whatsapp/templates/:id",
    ]);
  });

  it("refuses the create flow to a caller without the write grant", async () => {
    const element = routes.find((route) => route.path === "/whatsapp/templates/new")?.element;
    render(
      <PermissionProvider grants={["engage.whatsapp.template.read:ORG"]}>
        <MemoryRouter>{element}</MemoryRouter>
      </PermissionProvider>,
    );
    expect(await screen.findByText("You can't open the template editor right now")).toBeTruthy();
    expect(screen.queryByLabelText("Template name")).toBeNull();
  });

  it("opens the create flow for a caller with it", async () => {
    const element = routes.find((route) => route.path === "/whatsapp/templates/new")?.element;
    render(
      <PermissionProvider grants={READ_AND_WRITE}>
        <MemoryRouter>{element}</MemoryRouter>
      </PermissionProvider>,
    );
    expect(await screen.findByLabelText("Template name")).toBeTruthy();
  });
});
