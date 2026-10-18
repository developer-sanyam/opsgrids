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
    id: "lead-interest-check",
    name: "Lead interest check and follow-up",
    description:
      "Send leads an interest-check email, branch on interest: proceed further, block/disqualify if not interested, or send a follow-up email if no reply.",
    build: () => {
      let graph = addStep(emptyGraph("crm.lead.created"), { from: "trigger" }, "send_message").graph;
      graph = replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "email",
          template: { ref: "" },
          recipient_path: "payload.email",
          variable_map: { name: "payload.full_name", company: "payload.company" },
          purpose: "marketing",
        },
      });
      graph = addStep(graph, { from: "send_message_1" }, "wait").graph;
      graph = replaceNode(graph, {
        id: "wait_1",
        type: "wait",
        config: {
          seconds: 259_200,
          window: { start: "09:00", end: "18:00", timezone: "Asia/Kolkata" },
        },
      });
      graph = addStep(graph, { from: "wait_1" }, "condition").graph;
      graph = replaceNode(graph, {
        id: "condition_1",
        type: "condition",
        config: {
          match: "any",
          rules: [
            { path: "payload.status", op: "eq", value: "qualified" },
            { path: "payload.status", op: "eq", value: "working" },
          ],
        },
      });
      graph = addStep(graph, { from: "condition_1", label: "yes" }, "create_task").graph;
      graph = replaceNode(graph, {
        id: "create_task_1",
        type: "create_task",
        config: {
          title: "Follow up interested lead",
          description: "Lead expressed interest. Proceed with discovery call or demo.",
          assignee_team: "sales",
        },
      });
      graph = addStep(graph, { from: "condition_1", label: "no" }, "condition").graph;
      graph = replaceNode(graph, {
        id: "condition_2",
        type: "condition",
        config: {
          match: "all",
          rules: [{ path: "payload.status", op: "eq", value: "disqualified" }],
        },
      });
      graph = addStep(graph, { from: "condition_2", label: "yes" }, "create_task").graph;
      graph = replaceNode(graph, {
        id: "create_task_2",
        type: "create_task",
        config: {
          title: "Mark lead as blocked / disqualified",
          description: "Lead indicated no interest. Mark as disqualified and suppress future outreach.",
          assignee_team: "sales",
        },
      });
      graph = addStep(graph, { from: "condition_2", label: "no" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_2",
        type: "send_message",
        config: {
          channel: "email",
          template: { ref: "" },
          recipient_path: "payload.email",
          variable_map: { name: "payload.full_name", company: "payload.company" },
          purpose: "marketing",
        },
      });
    },
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
