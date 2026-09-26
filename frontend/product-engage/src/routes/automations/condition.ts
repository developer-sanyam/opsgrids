import type { ConditionOp, ConditionRule } from "@/api/automations";

/** How a condition rule's value moves between what is typed and what the graph stores. */

/** A number only when it reads back exactly, so half-typed `1.` stays text until it is `1.5`. */
function scalar(text: string): string | number {
  const trimmed = text.trim();
  return trimmed !== "" && String(Number(trimmed)) === trimmed ? Number(trimmed) : text;
}

/** What the value box shows for a rule's stored value. */
export function valueText(rule: ConditionRule): string {
  const { value } = rule;
  if (value === undefined) return "";
  return Array.isArray(value) ? value.join(", ") : String(value);
}

/** The value a rule stores for what was typed, shaped for its operator. `exists` takes none. */
export function parseValue(op: ConditionOp, text: string): ConditionRule["value"] | undefined {
  if (op === "exists") return undefined;
  if (op === "in") {
    return text
      .split(",")
      .map((part) => part.trim())
      .filter((part) => part !== "")
      .map(scalar);
  }
  if (op === "contains") return text;
  return scalar(text);
}
