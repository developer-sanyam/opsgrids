import { today } from "@/lib/now";

/** The date check every operation shares; `null` when fine. */
export function occurredOnError(occurredOn: string): string | null {
  if (occurredOn === "") return "Choose the date this happened.";
  if (occurredOn > today()) return "The date can't be in the future.";
  return null;
}
