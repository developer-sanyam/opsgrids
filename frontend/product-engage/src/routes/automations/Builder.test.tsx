import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";

import * as api from "@/api/automations";
import type { Graph } from "@/api/automations";
import {
  AUTOMATION_ACTIVE,
  AUTOMATION_DRAFT,
  DRAFT_GRAPH,
  EMAIL_TEMPLATE,
  TRIGGERS,
  WHATSAPP_TEMPLATE,
  detail,
  page,
} from "@/api/automations.fixtures";
import { BuilderPage } from "./Builder";
import { addStep, emptyGraph } from "./graph";

/**
 * The builder as a person uses it: adding a branch, deleting a step, mapping variables, and the
 * difference between saving (always allowed) and publishing (blocked until the flow is complete,
 * with the reason on the page).
 */

beforeEach(() => {
  vi.spyOn(api, "listTriggers").mockResolvedValue(TRIGGERS);
  vi.spyOn(api, "listWhatsAppTemplateOptions").mockResolvedValue([WHATSAPP_TEMPLATE]);
  vi.spyOn(api, "listEmailTemplateOptions").mockResolvedValue([EMAIL_TEMPLATE]);
  vi.spyOn(api, "listRuns").mockResolvedValue(page([]));
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderBuilder(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/automations/new" element={<BuilderPage />} />
        <Route path="/automations/:automationId" element={<BuilderPage />} />
        <Route path="/automations" element={<p>List page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function openDraft(graph: Graph = DRAFT_GRAPH, problems: api.ValidationProblem[] = []) {
  vi.spyOn(api, "getAutomation").mockResolvedValue(detail(AUTOMATION_DRAFT, graph, problems));
  return renderBuilder(`/automations/${AUTOMATION_DRAFT.id}`);
}

async function addFromMenu(menuName: string, item: string) {
  await userEvent.click(await screen.findByRole("button", { name: menuName }));
  await userEvent.click(await screen.findByRole("menuitem", { name: item }));
}

describe("loading and failure", () => {
  it("shows a loading status, then the flow", async () => {
    vi.spyOn(api, "getAutomation").mockReturnValue(new Promise(() => {}));
    renderBuilder(`/automations/${AUTOMATION_DRAFT.id}`);
    expect(screen.getByRole("status").textContent).toBe("Loading automation");
  });

  it("says what failed and retries", async () => {
    const get = vi.spyOn(api, "getAutomation").mockRejectedValueOnce(new ApiError(500, "The service is unavailable.", null));
    renderBuilder(`/automations/${AUTOMATION_DRAFT.id}`);
    expect(await screen.findByText("Couldn't load this automation")).toBeTruthy();
    get.mockResolvedValue(detail(AUTOMATION_DRAFT, DRAFT_GRAPH));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Automation flow" })).toBeTruthy();
  });

  it("shows the trigger's own error with a retry when events cannot be listed", async () => {
    vi.spyOn(api, "getAutomation").mockResolvedValue(detail(AUTOMATION_DRAFT, DRAFT_GRAPH));
    const triggers = vi.spyOn(api, "listTriggers").mockRejectedValueOnce(new ApiError(503, "Events are unavailable.", null));
    renderBuilder(`/automations/${AUTOMATION_DRAFT.id}`);
    expect(await screen.findByText("Couldn't load the events you can start from")).toBeTruthy();
    triggers.mockResolvedValue(TRIGGERS);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText(/^Event/)).toBeTruthy();
  });
});

describe("editing the flow", () => {
  it("adds a condition with a yes path and a no path, and puts a step on the no path", async () => {
    openDraft();
    await addFromMenu("Add step after step 2", "Condition");

    expect(await screen.findByRole("region", { name: "Yes path of step 3" })).toBeTruthy();
    const no = screen.getByRole("region", { name: "No path of step 3" });
    expect(within(no).getByRole("button", { name: "Add step to no path of step 3" })).toBeTruthy();
    expect(document.activeElement?.getAttribute("data-step-button")).toBe("condition_1");

    await addFromMenu("Add step to no path of step 3", "Create task");
    const task = await within(screen.getByRole("region", { name: "No path of step 3" })).findByText("Give the task a title");
    expect(task).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Yes path of step 3" })).queryByText("Give the task a title")).toBeNull();
  });

  it("deletes a step and closes the gap", async () => {
    openDraft();
    await userEvent.click(await screen.findByRole("button", { name: "Delete step 2, Wait" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Delete step 2, Wait" })).toBeNull());
    expect(screen.queryByText("10 minutes")).toBeNull();
    expect(document.activeElement?.getAttribute("data-step-button")).toBe("trigger");
  });

  it("asks before deleting a condition that has steps on its paths", async () => {
    let graph = addStep(emptyGraph("crm.lead.created"), { from: "trigger" }, "condition").graph;
    graph = addStep(graph, { from: "condition_1", label: "yes" }, "wait").graph;
    openDraft(graph);
    await userEvent.click(await screen.findByRole("button", { name: "Delete step 2, Condition" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/the 1 step on its yes and no paths/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete condition" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Delete step 2, Condition" })).toBeNull());
  });

  it("reorders two steps and never offers to move the trigger", async () => {
    let graph = addStep(emptyGraph("crm.lead.created"), { from: "trigger" }, "wait").graph;
    graph = addStep(graph, { from: "wait_1" }, "create_task").graph;
    openDraft(graph);
    const flow = await screen.findByRole("region", { name: "Automation flow" });
    expect(within(flow).queryByRole("button", { name: /Move step 1/ })).toBeNull();
    await userEvent.click(within(flow).getByRole("button", { name: "Move step 2, Wait down" }));
    const steps = within(flow).getAllByRole("button", { name: /^\d · / });
    expect(steps.map((b) => b.getAttribute("data-step-button"))).toEqual(["trigger", "create_task_1", "wait_1"]);
  });
});

describe("mapping variables", () => {
  it("maps a template's variables from the trigger's, previews the message, and saves the map", async () => {
    const create = vi.spyOn(api, "createAutomation").mockResolvedValue({ ...AUTOMATION_DRAFT, id: "new-id" });
    vi.spyOn(api, "getAutomation").mockResolvedValue(detail({ ...AUTOMATION_DRAFT, id: "new-id" }, DRAFT_GRAPH));
    renderBuilder("/automations/new");

    await userEvent.type(await screen.findByLabelText(/^Name/), "Payment reminder");
    await userEvent.selectOptions(await screen.findByLabelText(/^Event/), "finance.billing.invoice.issued");
    await addFromMenu("Add step after step 1", "Send message");

    await userEvent.selectOptions(await screen.findByLabelText(/^Template/), "payment_reminder");
    await userEvent.type(screen.getByLabelText(/^Send to/), "payload.customer.phone");
    await userEvent.type(screen.getByLabelText(/Value for \{\{1\}\}/), "payload.customer.name");
    await userEvent.type(screen.getByLabelText(/Value for \{\{2\}\}/), "payload.invoice.total");

    expect(screen.getByTestId("message-preview").textContent).toBe("Hi [Customer name], your invoice total is [Invoice total].");

    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const sent = create.mock.calls[0]?.[0];
    const send = sent?.graph.nodes.find((n) => n.type === "send_message");
    expect(send?.type === "send_message" && send.config).toEqual({
      channel: "whatsapp",
      template: { ref: "payment_reminder", language: "en" },
      recipient_path: "payload.customer.phone",
      variable_map: { "1": "payload.customer.name", "2": "payload.invoice.total" },
    });
  });

  it("names each unmapped variable on the step until it is mapped", async () => {
    openDraft(emptyGraph("finance.billing.invoice.issued"));
    await addFromMenu("Add step after step 1", "Send message");
    await userEvent.selectOptions(await screen.findByLabelText(/^Template/), "payment_reminder");
    const flow = screen.getByRole("region", { name: "Automation flow" });
    expect(await within(flow).findByText("Map a value for {{1}}.")).toBeTruthy();
    await userEvent.type(screen.getByLabelText(/Value for \{\{1\}\}/), "payload.customer.name");
    await waitFor(() => expect(within(flow).queryByText("Map a value for {{1}}.")).toBeNull());
    expect(within(flow).getByText("Map a value for {{2}}.")).toBeTruthy();
  });

  it("switches the send step to email and offers email templates instead", async () => {
    openDraft(emptyGraph("finance.billing.invoice.issued"));
    await addFromMenu("Add step after step 1", "Send message");
    await userEvent.selectOptions(await screen.findByLabelText(/^Channel/), "email");
    const template = await screen.findByLabelText(/^Template/);
    await waitFor(() => expect(within(template).getByRole("option", { name: "order_shipped" })).toBeTruthy());
    expect(within(template).queryByRole("option", { name: /payment_reminder/ })).toBeNull();
  });

  it("says when a channel's templates cannot be loaded and offers a retry", async () => {
    const list = vi.spyOn(api, "listWhatsAppTemplateOptions").mockRejectedValueOnce(new ApiError(502, "Meta is unavailable.", null));
    openDraft(emptyGraph("finance.billing.invoice.issued"));
    await addFromMenu("Add step after step 1", "Send message");
    expect(await screen.findByText("Couldn't load WhatsApp templates")).toBeTruthy();
    list.mockResolvedValue([WHATSAPP_TEMPLATE]);
    const alert = screen.getByText("Couldn't load WhatsApp templates").closest("[role='alert']") as HTMLElement;
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText(/^Template/)).toBeTruthy();
  });
});

describe("publishing", () => {
  it("is blocked, with the reason, until the flow is complete", async () => {
    openDraft(addStep(emptyGraph("crm.lead.created"), { from: "trigger" }, "condition").graph);
    const publish = await screen.findByRole("button", { name: "Publish" });
    expect((publish as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Fix 1 thing to publish")).toBeTruthy();
    expect(publish.getAttribute("aria-describedby")).toBe("publish-blockers");
    const flow = screen.getByRole("region", { name: "Automation flow" });
    expect(within(flow).getByText("Add at least one rule.")).toBeTruthy();

    await userEvent.click(within(flow).getByRole("button", { name: /2 · Condition/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    await userEvent.type(screen.getByLabelText(/Field for rule 1/), "payload.lead.name");
    await userEvent.type(screen.getByLabelText(/Value for rule 1/), "Rahul");

    await waitFor(() => expect((screen.getByRole("button", { name: "Publish" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByText("Ready to publish")).toBeTruthy();
  });

  it("saves, publishes and returns to the list when the service accepts", async () => {
    const update = vi.spyOn(api, "updateAutomation").mockResolvedValue(AUTOMATION_DRAFT);
    const publish = vi.spyOn(api, "publishAutomation").mockResolvedValue({ ...AUTOMATION_DRAFT, status: "active" });
    openDraft();
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    await waitFor(() => expect(publish).toHaveBeenCalledWith(AUTOMATION_DRAFT.id));
    expect(update).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("List page")).toBeTruthy();
  });

  it("writes the service's problems on the step they name, and keeps what was entered", async () => {
    vi.spyOn(api, "updateAutomation").mockResolvedValue(AUTOMATION_DRAFT);
    const problem = { problems: [{ node_id: "wait_1", code: "wait_window", message: "This wait needs a sending window." }] } as unknown as ProblemJson;
    vi.spyOn(api, "publishAutomation").mockRejectedValue(new ApiError(422, "Validation failed", problem));
    openDraft();
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    const flow = screen.getByRole("region", { name: "Automation flow" });
    expect(await within(flow).findByText("This wait needs a sending window.")).toBeTruthy();
    expect(screen.getByText("Publishing was refused. The steps that need fixing are marked in the flow.")).toBeTruthy();
    expect((screen.getByLabelText(/^Name/) as HTMLInputElement).value).toBe(AUTOMATION_DRAFT.name);
    // The refusal now blocks publishing, with the reason listed, until the flow is edited.
    expect((screen.getByRole("button", { name: "Publish" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Fix 1 thing to publish")).toBeTruthy();
    await userEvent.click(within(flow).getByRole("button", { name: "Delete step 2, Wait" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Publish" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("leaves at once on Cancel when there is nothing unsaved", async () => {
    openDraft();
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("List page")).toBeTruthy();
  });

  it("asks before Cancel throws away unsaved changes, and keeps them on Keep editing", async () => {
    openDraft();
    await userEvent.type(await screen.findByLabelText(/^Name/), " v2");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Discard your changes?")).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "Keep editing" }));
    expect((screen.getByLabelText(/^Name/) as HTMLInputElement).value).toBe(`${AUTOMATION_DRAFT.name} v2`);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("List page")).toBeTruthy();
  });

  it("refuses to save without a name and says so on the field", async () => {
    const create = vi.spyOn(api, "createAutomation");
    renderBuilder("/automations/new");
    await userEvent.click(await screen.findByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Give the automation a name.", { selector: "p, span, div" })).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("does not submit twice while a save is in flight", async () => {
    let resolve: (row: typeof AUTOMATION_DRAFT) => void = () => {};
    const update = vi.spyOn(api, "updateAutomation").mockReturnValue(new Promise((r) => (resolve = r)));
    openDraft();
    const save = await screen.findByRole("button", { name: "Save draft" });
    await userEvent.click(save);
    expect((await screen.findByRole("button", { name: "Saving…" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Publish" }) as HTMLButtonElement).disabled).toBe(true);
    resolve(AUTOMATION_DRAFT);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  });
});

describe("a published automation", () => {
  it("is read only, offers pause, and lists no edit controls", async () => {
    vi.spyOn(api, "getAutomation").mockResolvedValue(detail(AUTOMATION_ACTIVE, DRAFT_GRAPH));
    renderBuilder(`/automations/${AUTOMATION_ACTIVE.id}`);
    expect(await screen.findByText("This automation is active")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Add step/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete step/ })).toBeNull();
    expect((screen.getByLabelText(/^Name/) as HTMLInputElement).disabled).toBe(true);
  });

  it("shows its runs on the Runs tab", async () => {
    vi.spyOn(api, "getAutomation").mockResolvedValue(detail(AUTOMATION_ACTIVE, DRAFT_GRAPH));
    renderBuilder(`/automations/${AUTOMATION_ACTIVE.id}`);
    await userEvent.click(await screen.findByRole("tab", { name: "Runs" }));
    expect(await screen.findByText("No runs yet")).toBeTruthy();
  });
});

describe("a send step that looks a contact up", () => {
  const CONTACT_HELPER =
    "The CRM contact on the event. The WhatsApp number is looked up from the contact when the message is sent.";
  const ADDRESS_HELPER = "Where the phone number is read from on the event.";

  const LOOKUP_GRAPH: Graph = {
    nodes: [
      { id: "trigger", type: "trigger", config: { event_type: "crm.lead.converted" } },
      {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "whatsapp",
          template: { ref: "" },
          recipient_path: "payload.contact_id",
          recipient_kind: "contact_id",
          variable_map: {},
          purpose: "marketing",
        },
      },
    ],
    edges: [{ from: "trigger", to: "send_message_1" }],
  };

  /** Open the draft and select its send step. Returns the spy the save goes through. */
  async function openStep() {
    const update = vi.spyOn(api, "updateAutomation").mockResolvedValue(AUTOMATION_DRAFT);
    openDraft(LOOKUP_GRAPH);
    const flow = await screen.findByRole("region", { name: "Automation flow" });
    await userEvent.click(within(flow).getByRole("button", { name: /2 · Send message/ }));
    return update;
  }

  /** Save, then read the send step's config the way the service would receive it. */
  async function savedConfig(update: MockInstance<typeof api.updateAutomation>) {
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const send = update.mock.calls[0]?.[1].graph?.nodes.find((n) => n.type === "send_message");
    if (send?.type !== "send_message") throw new Error("the saved graph has no send step");
    return send.config;
  }

  it("says where the number comes from, and keeps the kind through a template pick and a purpose change", async () => {
    const update = await openStep();
    expect(await screen.findByText(CONTACT_HELPER)).toBeTruthy();
    expect((screen.getByLabelText(/^Send to/) as HTMLInputElement).placeholder).toBe("payload.contact_id");

    await userEvent.selectOptions(await screen.findByLabelText(/^Template/), "payment_reminder");
    await userEvent.selectOptions(screen.getByLabelText(/^Purpose/), "transactional");

    expect(screen.getByText(CONTACT_HELPER)).toBeTruthy();
    const config = await savedConfig(update);
    expect(config.recipient_kind).toBe("contact_id");
    expect(config.template.ref).toBe("payment_reminder");
    expect(config.purpose).toBe("transactional");
  });

  it("drops the kind when the channel becomes email and does not bring it back with WhatsApp", async () => {
    const update = await openStep();
    await userEvent.selectOptions(await screen.findByLabelText(/^Channel/), "email");
    expect(await screen.findByText("Where the email address is read from on the event.")).toBeTruthy();
    expect(screen.queryByText(CONTACT_HELPER)).toBeNull();

    await userEvent.selectOptions(screen.getByLabelText(/^Channel/), "whatsapp");
    expect(await screen.findByText(ADDRESS_HELPER)).toBeTruthy();
    expect(screen.queryByText(CONTACT_HELPER)).toBeNull();

    const config = await savedConfig(update);
    expect(config).not.toHaveProperty("recipient_kind");
    expect(config.channel).toBe("whatsapp");
  });

  it("keeps the kind for another path that names a contact id, and drops it for one that does not", async () => {
    const update = await openStep();
    const sendTo = await screen.findByLabelText(/^Send to/);

    fireEvent.change(sendTo, { target: { value: "payload.customer.contact_id" } });
    expect(screen.getByText(CONTACT_HELPER)).toBeTruthy();

    fireEvent.change(sendTo, { target: { value: "payload.customer.phone" } });
    expect(screen.getByText(ADDRESS_HELPER)).toBeTruthy();
    expect(screen.queryByText(CONTACT_HELPER)).toBeNull();

    const config = await savedConfig(update);
    expect(config).not.toHaveProperty("recipient_kind");
    expect(config.recipient_path).toBe("payload.customer.phone");
  });

  /* Characterization of the design's second rule under the edit a person really makes: typing in the
     field. Every keystroke is one change, so deleting a letter of `contact_id` and typing it back
     passes through a path that names no contact id, and the kind is gone for good (there is no
     control to choose it). The helper text flips at once, which is the only signal, and the service
     refuses the id as an address (`test_a_contact_id_read_as_an_address_is_refused_and_nothing_is_sent`
     in `test_crm_lead_keys.py`), so nothing is sent to the wrong person. A reviewer who prefers to
     restore the kind when the path names a contact id again flips this one test and gives
     `SendSettings` a ref that remembers the kind the step was opened with (and `StepSettings` a
     `key={node.id}` so it is not shared between steps). */
  it("loses the kind on the first edit that stops naming a contact id, and typing the path back does not restore it", async () => {
    const update = await openStep();
    const sendTo = await screen.findByLabelText(/^Send to/);

    fireEvent.change(sendTo, { target: { value: "payload.contact_i" } });
    expect(screen.getByText(ADDRESS_HELPER)).toBeTruthy();

    fireEvent.change(sendTo, { target: { value: "payload.contact_id" } });
    expect(screen.getByText(ADDRESS_HELPER)).toBeTruthy();
    expect(screen.queryByText(CONTACT_HELPER)).toBeNull();

    const config = await savedConfig(update);
    expect(config.recipient_path).toBe("payload.contact_id");
    expect(config).not.toHaveProperty("recipient_kind");
  });
});
