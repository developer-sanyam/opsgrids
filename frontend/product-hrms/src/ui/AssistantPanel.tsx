import { useState } from "react";

import { cn } from "@/lib/cn";
import { Card } from "./Card";
import { SparkIcon, SendIcon } from "./icons";

const QUICK_ACTIONS = [
  "Who's on leave today?",
  "Run attendance report",
  "Add an employee",
] as const;

/**
 * The HR assistant — PlanIQ's dark emphasis card, reframed for HR. A greeting, a prompt field,
 * and quick-action chips. This slice wires the affordance, not a live model; submitting is inert
 * and the field stays a real, labelled input.
 */
export function AssistantPanel({ name }: { name: string }) {
  const [value, setValue] = useState("");
  const [picked, setPicked] = useState<string | null>(null);

  return (
    <Card tone="dark" className="p-6 flex flex-col gap-5">
      <div className="flex items-center gap-2 text-mint">
        <span className="grid size-8 place-items-center rounded-full bg-mint/15">
          <SparkIcon className="size-4" />
        </span>
        <span className="text-sm font-semibold tracking-wide">HR Assistant</span>
      </div>

      <div className="flex flex-col gap-1">
        <h2 className="display text-2xl font-bold leading-tight">
          Good morning, {name}.
        </h2>
        <p className="text-sm text-white/60">How can I help?</p>
      </div>

      <form
        className="flex items-center gap-2 rounded-full bg-white/10 pl-4 pr-1.5 h-12 focus-within:outline focus-within:outline-2 focus-within:outline-mint focus-within:outline-offset-2"
        onSubmit={(e) => {
          e.preventDefault();
          // Inert until the assistant service exists; keeps the input honest meanwhile.
        }}
      >
        <label htmlFor="hr-assistant-prompt" className="sr-only">
          Ask the HR assistant
        </label>
        <input
          id="hr-assistant-prompt"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Ask me anything about your team…"
          className="min-w-0 flex-1 bg-transparent text-sm text-white placeholder:text-white/40 focus:outline-none"
        />
        <button
          type="submit"
          aria-label="Send message"
          className="grid size-9 shrink-0 place-items-center rounded-full bg-mint text-ink transition-colors hover:bg-mint-strong"
        >
          <SendIcon className="size-4" />
        </button>
      </form>

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-white/40">
          Quick actions
        </span>
        <div className="flex flex-wrap gap-2">
          {QUICK_ACTIONS.map((action) => (
            <button
              key={action}
              type="button"
              aria-pressed={picked === action}
              onClick={() => {
                setPicked(action);
                setValue(action);
              }}
              className={cn(
                "rounded-full px-3.5 h-9 text-sm font-medium transition-colors",
                picked === action
                  ? "bg-mint text-ink"
                  : "bg-white/10 text-white/80 hover:bg-white/20",
              )}
            >
              {action}
            </button>
          ))}
        </div>
      </div>
    </Card>
  );
}
