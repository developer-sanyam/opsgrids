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
    id: "welcome-converted-lead",
    name: "Welcome a converted lead",
    description: "When a lead is converted, send the new contact a WhatsApp welcome message, if they have opted in.",
    build: () => {
      const graph = addStep(emptyGraph("crm.lead.converted"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
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
      });
    },
  },
  {
    id: "invoice-follow-up",
    name: "Invoice follow-up",
    description: "The day after an invoice's due date, if it is still unpaid, send the customer one WhatsApp reminder. Needs the customer linked to a CRM contact.",
    build: () => {
      const graph = addStep(emptyGraph("finance.billing.invoice.overdue"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "whatsapp",
          template: { ref: "" },
          recipient_path: "payload.crm_contact_id",
          recipient_kind: "contact_id",
          variable_map: {},
          purpose: "transactional",
        },
      });
    },
  },
  {
    id: "payment-receipt",
    name: "Payment receipt",
    description: "When a payment is received, send the customer a WhatsApp receipt. Needs the customer linked to a CRM contact.",
    build: () => {
      const graph = addStep(emptyGraph("finance.billing.payment.received"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "whatsapp",
          template: { ref: "" },
          recipient_path: "payload.crm_contact_id",
          recipient_kind: "contact_id",
          variable_map: {},
          purpose: "transactional",
        },
      });
    },
  },
  {
    id: "invoice-issued-notice",
    name: "Invoice issued notice",
    description: "When an invoice is issued, send the customer a WhatsApp notice. Needs the customer linked to a CRM contact.",
    build: () => {
      const graph = addStep(emptyGraph("finance.billing.invoice.issued"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "whatsapp",
          template: { ref: "" },
          recipient_path: "payload.crm_contact_id",
          recipient_kind: "contact_id",
          variable_map: {},
          purpose: "transactional",
        },
      });
    },
  },
  {
    id: "deal-won",
    name: "Thank a customer for a won deal",
    description: "When a deal is marked won, send the deal's primary contact a WhatsApp thank-you, if they have opted in.",
    build: () => {
      let graph = addStep(emptyGraph("crm.pipeline.deal.stage_changed"), { from: "trigger" }, "condition").graph;
      graph = replaceNode(graph, {
        id: "condition_1",
        type: "condition",
        config: { match: "all", rules: [{ path: "payload.to_outcome", op: "eq", value: "won" }] },
      });
      graph = addStep(graph, { from: "condition_1", label: "yes" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "whatsapp",
          template: { ref: "" },
          recipient_path: "payload.crm_contact_id",
          recipient_kind: "contact_id",
          variable_map: {},
          purpose: "marketing",
        },
      });
    },
  },
  {
    id: "lead-follow-up",
    name: "Follow up a new lead",
    description: "When a lead comes in, ask the sales team to follow up.",
    build: () => addStep(emptyGraph("crm.lead.created"), { from: "trigger" }, "create_task").graph,
  },
  {
    id: "low-stock-alert",
    name: "Low-stock alert",
    description: "When an item falls to or below its reorder point, email whoever operations has set as the alert contact.",
    build: () => {
      const graph = addStep(emptyGraph("operations.inventory.item.low_stock"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "email",
          template: { ref: "" },
          recipient_path: "payload.alert_contact.email",
          variable_map: { sku: "payload.sku", name: "payload.name", on_hand: "payload.on_hand" },
        },
      });
    },
  },
  {
    id: "delayed-task-alert",
    name: "Delayed pick-task alert",
    description: "When a pick task has sat open past the warehouse's SLA, email the alert contact.",
    build: () => {
      const graph = addStep(emptyGraph("operations.fulfilment.task.delayed"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "email",
          template: { ref: "" },
          recipient_path: "payload.alert_contact.email",
          variable_map: { task_id: "payload.task_id", age_minutes: "payload.age_minutes" },
        },
      });
    },
  },
  {
    id: "delayed-shipment-alert",
    name: "Delayed shipment alert",
    description: "When a shipment has sat non-terminal past the warehouse's SLA, email the alert contact.",
    build: () => {
      const graph = addStep(emptyGraph("operations.dispatch.shipment.delayed"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "email",
          template: { ref: "" },
          recipient_path: "payload.alert_contact.email",
          variable_map: { shipment_id: "payload.shipment_id", age_minutes: "payload.age_minutes" },
        },
      });
    },
  },
  {
    id: "task-escalated",
    name: "Pick-task escalation",
    description: "When a pick task exhausts its offer rounds and escalates, email the alert contact.",
    build: () => {
      const graph = addStep(emptyGraph("operations.fulfilment.task.escalated"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "email",
          template: { ref: "" },
          recipient_path: "payload.alert_contact.email",
          variable_map: { task_id: "payload.task_id", round: "payload.round" },
        },
      });
    },
  },
];

export function findRecipe(id: string | null): Recipe | undefined {
  return RECIPES.find((r) => r.id === id);
}
