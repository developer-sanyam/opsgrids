import { cn } from "../lib/cn";
import { statusLabel, statusTone } from "../lib/status";
import type { StatusTone, StatusWord } from "../lib/status";

/** Each tone is a token family's ink on its own tint; every pair is AA in both themes. */
const TONE: Record<StatusTone, string> = {
  success: "bg-present-soft text-present",
  warning: "bg-late-soft text-late",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
  neutral: "bg-neutral-soft text-neutral",
};

interface Common {
  className?: string | undefined;
}

/** A registered word: the registry decides the tone, so the word looks the same everywhere. */
interface ByStatus extends Common {
  status: StatusWord;
  /** The product's own wording for the word ("Past due", "Awaiting payment"). Defaults to it. */
  label?: string | undefined;
  tone?: never;
}

/** A product's own word, placed on one of the five tones. The label is required: it is the word. */
interface ByTone extends Common {
  tone: StatusTone;
  label: string;
  status?: never;
}

export type StatusBadgeProps = ByStatus | ByTone;

/**
 * A status, in words, on its tone's tint: "Active", "Trial", "Overdue".
 *
 * Pass a registered `status` and the colour follows from the word (lib/status.ts); there is no
 * tone override, because an override is how the same word came to look different in every app.
 * A word the registry does not know takes an explicit `tone` and `label` instead.
 *
 * Meaning never rests on the colour: the word is always there, and a screen reader reads just the
 * word. It is static text, not a live region — a status that changes while the user watches is
 * announced by whatever changed it.
 */
export function StatusBadge(props: StatusBadgeProps) {
  const tone = props.status !== undefined ? statusTone(props.status) : props.tone;
  const label = props.label ?? (props.status !== undefined ? statusLabel(props.status) : "");
  return (
    <span
      data-tone={tone}
      className={cn(
        "inline-flex max-w-full items-center rounded-chip px-2 py-0.5 text-caption font-semibold whitespace-nowrap",
        TONE[tone],
        props.className,
      )}
    >
      <span className="truncate">{label}</span>
    </span>
  );
}
