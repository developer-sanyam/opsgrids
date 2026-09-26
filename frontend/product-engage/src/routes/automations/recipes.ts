import type { Graph } from "@/api/automations";

import { addStep, emptyGraph, replaceNode } from "./graph";

/**
 * Starting points for an empty list. Each has its trigger chosen and its steps in place; what a
 * recipe cannot know (which template, which variables) is left for the person to fill in, and the
 * builder flags each step until they do. The trigger events are ones the platform publishes today;
 * if the service's catalogue does not offer one, the builder says so on the trigger rather than
 * pretending.
 */

export interface Recipe {
  id: string;
  name: string;
  description: string;
  build: () => Graph;
}

export const RECIPES: Recipe[] = [
  {
    id: "welcome-new-lead",
    name: "Welcome a new lead",
    description: "When a lead is created, send a WhatsApp welcome message.",
    build: () => addStep(emptyGraph("crm.lead.created"), { from: "trigger" }, "send_message").graph,
  },
  {
    id: "invoice-follow-up",
    name: "Invoice follow-up",
    description: "When an invoice is issued, wait three days and send a payment reminder.",
    build: () => {
      let graph = addStep(emptyGraph("finance.billing.invoice.issued"), { from: "trigger" }, "wait").graph;
      graph = replaceNode(graph, { id: "wait_1", type: "wait", config: { seconds: 259_200 } });
      return addStep(graph, { from: "wait_1" }, "send_message").graph;
    },
  },
  {
    id: "payment-receipt",
    name: "Payment receipt",
    description: "When a payment is received, email the customer a receipt.",
    build: () => {
      const graph = addStep(emptyGraph("finance.billing.payment.received"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: { channel: "email", template: { ref: "" }, recipient_path: "", variable_map: {} },
      });
    },
  },
  {
    id: "enquiry-follow-up",
    name: "Follow up a storefront enquiry",
    description: "When an enquiry comes in, ask the sales team to follow up.",
    build: () => addStep(emptyGraph("storefront.enquiry.created"), { from: "trigger" }, "create_task").graph,
  },
];

export function findRecipe(id: string | null): Recipe | undefined {
  return RECIPES.find((r) => r.id === id);
}
