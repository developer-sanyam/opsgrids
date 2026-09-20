/**
 * Reading a section's settings safely.
 *
 * A `SectionInstance` carries whatever the merchant last saved, which is not necessarily what the
 * section library declares today: a field can be added, and a document written before it existed
 * simply has no key. Every read goes through here and falls back, so a section renders from a
 * partial document instead of throwing on `undefined`.
 */

import type { SettingValue } from "../schema";

export type Settings = Record<string, SettingValue>;

export function str(settings: Settings, key: string, fallback = ""): string {
  const value = settings[key];
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return fallback;
}

export function num(settings: Settings, key: string, fallback: number): number {
  const value = settings[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

/** A number clamped to a range — column counts, item limits, anything the editor can over-type. */
export function clampNum(settings: Settings, key: string, fallback: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(num(settings, key, fallback))));
}

export function bool(settings: Settings, key: string, fallback: boolean): boolean {
  const value = settings[key];
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;
  return fallback;
}

/** A value constrained to a known set — `select` fields, where an unknown string means "default". */
export function choice<T extends string>(
  settings: Settings,
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  const value = settings[key];
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}

/** A trimmed string, or null when the merchant left the field empty. */
export function optional(settings: Settings, key: string): string | null {
  const value = str(settings, key).trim();
  return value === "" ? null : value;
}

/** Split a textarea whose convention is one entry per line. */
export function lines(settings: Settings, key: string): string[] {
  return str(settings, key)
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}
