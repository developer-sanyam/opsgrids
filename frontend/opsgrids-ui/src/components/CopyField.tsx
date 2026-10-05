import { useEffect, useState } from "react";

import { CheckIcon, ClipboardListIcon } from "../icons";
import { useAnnounce } from "../lib/announcer";
import { cn } from "../lib/cn";
import { FIELD_LABEL } from "../lib/field";
import { Button } from "./Button";

interface CopyFieldProps {
  /** What the value is ("Webhook URL"). Also names the copy button for a screen reader. */
  label: string;
  /** The exact text to be pasted somewhere else. Never truncated, wrapped for looks or prettified. */
  value: string;
  helper?: string | undefined;
  className?: string | undefined;
}

/**
 * A value the user has to paste into another system — a webhook URL into a provider's dashboard,
 * a record into a registrar — shown whole, as text, with a Copy button beside it.
 *
 * The value is plain, selectable text (one click selects all of it), not a field: there is
 * nothing to edit, and a screen reader reads it in place. The clipboard can be refused (no
 * permission, an insecure origin), and then the honest answer is "select it and copy it yourself"
 * — said through the screen's live region, never a button that silently did nothing.
 */
export function CopyField({ label, value, helper, className }: CopyFieldProps) {
  const announce = useAnnounce();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(id);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      announce(`${label} copied.`);
    } catch {
      announce(`Couldn't copy the ${label.toLowerCase()}. Select it and copy it yourself.`);
    }
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <span className={FIELD_LABEL}>{label}</span>
      <div className="flex items-start gap-2">
        <code className="min-w-0 flex-1 select-all break-all rounded-tile border border-line bg-sunken p-3 font-mono text-xs text-ink">
          {value}
        </code>
        <Button
          type="button"
          variant="secondary"
          onClick={() => void copy()}
          aria-label={`Copy ${label}`}
          leadingIcon={copied ? <CheckIcon className="size-4 text-positive" /> : <ClipboardListIcon className="size-4" />}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      {helper ? <p className="text-xs text-muted">{helper}</p> : null}
    </div>
  );
}
