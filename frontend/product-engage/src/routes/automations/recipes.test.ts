import { describe, expect, it } from "vitest";

import { findRecipe, RECIPES } from "./recipes";

/**
 * The starter recipes are what an empty automation list offers, so what each one builds is a
 * promise to the person who picks it. The lead recipe is pinned whole, `purpose` included: it is
 * the first starter that messages a CRM contact straight from a CRM event, and it must only send
 * to a contact who has opted in.
 */

describe("the lead recipe", () => {
  it("is the converted-lead welcome, and the only recipe that starts on a lead event", () => {
    const leadRecipes = RECIPES.filter((recipe) => {
      const trigger = recipe.build().nodes.find((node) => node.type === "trigger");
      return trigger?.type === "trigger" && trigger.config.event_type.startsWith("crm.lead.");
    });
    expect(leadRecipes.map((recipe) => recipe.id)).toEqual(["welcome-converted-lead"]);
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
