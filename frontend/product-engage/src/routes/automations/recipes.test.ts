import { describe, expect, it } from "vitest";

import { findRecipe, RECIPES } from "./recipes";

/**
 * The starter recipes are what an empty automation list offers, so what each one builds is a
 * promise to the person who picks it. The lead recipe is pinned whole, `purpose` included: it is
 * the first starter that messages a CRM contact straight from a CRM event, and it must only send
 * to a contact who has opted in.
 */

describe("the lead recipes", () => {
  it("includes the converted-lead welcome, lead interest check, and new-lead follow-up recipes", () => {
    const leadRecipes = RECIPES.filter((recipe) => {
      const trigger = recipe.build().nodes.find((node) => node.type === "trigger");
      return trigger?.type === "trigger" && trigger.config.event_type.startsWith("crm.lead.");
    });
    expect(leadRecipes.map((recipe) => recipe.id)).toEqual([
      "welcome-converted-lead",
      "lead-interest-check",
      "lead-follow-up",
    ]);
    const recipe = findRecipe("welcome-converted-lead");
    expect(recipe?.name).toBe("Welcome a converted lead");
    expect(recipe?.description).toBe(
      "When a lead is converted, send the new contact a WhatsApp welcome message, if they have opted in.",
    );
  });

  it("starts on lead conversion and messages the converted contact as a marketing send", () => {
    expect(findRecipe("welcome-converted-lead")?.build()).toEqual({
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
    });
  });

  it("builds the lead-interest-check recipe with branching and follow-up", () => {
    const recipe = findRecipe("lead-interest-check");
    expect(recipe?.name).toBe("Lead interest check and follow-up");
    expect(recipe?.description).toBe(
      "Send leads an interest-check email, branch on interest: proceed further, block/disqualify if not interested, or send a follow-up email if no reply.",
    );

    const graph = recipe?.build();
    expect(graph).toBeDefined();
    if (!graph) return;

    expect(graph.nodes.map((n) => n.id)).toEqual([
      "trigger",
      "send_message_1",
      "wait_1",
      "condition_1",
      "end_1",
      "end_2",
      "create_task_1",
      "condition_2",
      "end_3",
      "create_task_2",
      "send_message_2",
    ]);

    expect(graph.edges).toEqual([
      { from: "trigger", to: "send_message_1" },
      { from: "send_message_1", to: "wait_1" },
      { from: "wait_1", to: "condition_1" },
      { from: "condition_1", to: "create_task_1", label: "yes" },
      { from: "create_task_1", to: "end_1" },
      { from: "condition_1", to: "condition_2", label: "no" },
      { from: "condition_2", to: "create_task_2", label: "yes" },
      { from: "create_task_2", to: "end_2" },
      { from: "condition_2", to: "send_message_2", label: "no" },
      { from: "send_message_2", to: "end_3" },
    ]);
  });
});

describe("the recipes that address a CRM contact from the event", () => {
  const sends = (id: string) =>
    findRecipe(id)
      ?.build()
      .nodes.find((node) => node.type === "send_message");

  it.each([
    ["payment-receipt", "transactional"],
    ["invoice-follow-up", "transactional"],
    ["invoice-issued-notice", "transactional"],
    ["deal-won", "marketing"],
  ])("%s looks the contact up from payload.crm_contact_id as %s", (id, purpose) => {
    expect(sends(id)?.config).toMatchObject({
      recipient_path: "payload.crm_contact_id",
      recipient_kind: "contact_id",
      purpose,
    });
  });

  it("deal-won sends only on the yes path of to_outcome == won", () => {
    const graph = findRecipe("deal-won")?.build();
    const condition = graph?.nodes.find((node) => node.type === "condition");
    expect(condition?.config).toEqual({
      match: "all",
      rules: [{ path: "payload.to_outcome", op: "eq", value: "won" }],
    });
    expect(graph?.edges).toContainEqual({ from: "condition_1", to: "send_message_1", label: "yes" });
  });

  it("the follow-up triggers on the overdue event, so a paid invoice never gets a reminder", () => {
    const trigger = findRecipe("invoice-follow-up")?.build().nodes.find((node) => node.type === "trigger");
    expect(trigger?.config).toEqual({ event_type: "finance.billing.invoice.overdue" });
  });
});
