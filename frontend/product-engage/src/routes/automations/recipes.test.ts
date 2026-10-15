import { describe, expect, it } from "vitest";

import { findRecipe, RECIPES } from "./recipes";

/**
 * The starter recipes are what an empty automation list offers, so what each one builds is a
 * promise to the person who picks it. The lead recipe is pinned whole, `purpose` included: it is
 * the first starter that messages a CRM contact straight from a CRM event, and it must only send
 * to a contact who has opted in.
 */

describe("the lead recipe", () => {
  it("is the converted-lead welcome, alongside the new-lead follow-up task", () => {
    const leadRecipes = RECIPES.filter((recipe) => {
      const trigger = recipe.build().nodes.find((node) => node.type === "trigger");
      return trigger?.type === "trigger" && trigger.config.event_type.startsWith("crm.lead.");
    });
    expect(leadRecipes.map((recipe) => recipe.id)).toEqual(["welcome-converted-lead", "lead-follow-up"]);
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
