import { AlertTriangleIcon, ArrowLeftIcon, ArrowRightIcon, CheckIcon } from "../icons";
import { cn } from "../lib/cn";
import { Button } from "./Button";

export interface StepItem {
  /** Stable key. */
  id: string;
  /** "Basic information", "Pricing & stock". */
  title: string;
  /** What the step asks for, in a few words: "Name, description, category". Shown from 768px. */
  description?: string | undefined;
  /** The step holds a validation error — marked in the list, so a long form's problem can be found. */
  invalid?: boolean | undefined;
}

interface StepperProps {
  steps: StepItem[];
  /** The index of the step on screen. */
  current: number;
  /**
   * The furthest step the user has reached, for coming back to it after going back. Steps up to
   * here are offered as links; steps beyond it are not, because Continue is where a step is
   * validated and skipping ahead would skip that. Defaults to `current`.
   */
  reached?: number | undefined;
  /** Called with a step's index when one is chosen from the list. Omit it for a read-only indicator. */
  onStepChange?: ((index: number) => void) | undefined;
  /** Names the step list for a screen reader. */
  label?: string | undefined;
  className?: string | undefined;
}

type Status = "complete" | "current" | "upcoming";

function statusOf(index: number, current: number): Status {
  if (index < current) return "complete";
  if (index === current) return "current";
  return "upcoming";
}

const STATUS_WORD: Record<Status, string> = {
  complete: "Completed",
  current: "",
  upcoming: "Not started",
};

function Marker({ index, status, invalid }: { index: number; status: Status; invalid: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-full text-sm font-bold tnum",
        invalid
          ? "bg-negative-soft text-negative"
          : status === "current"
            ? "bg-accent text-on-accent"
            : status === "complete"
              ? "bg-accent-soft text-accent-ink"
              : "border border-line bg-surface text-muted",
      )}
    >
      {invalid ? (
        <AlertTriangleIcon className="size-4" />
      ) : status === "complete" ? (
        <CheckIcon className="size-4" strokeWidth={2.4} />
      ) : (
        index + 1
      )}
    </span>
  );
}

/**
 * Where a long form is, and the way back to earlier steps (brief §8–9: Basic info → Pricing &
 * settings → Media → Additional details → Review).
 *
 * **Two layouts, one list.** From 768px the steps stand in a column beside the form, each with its
 * title and what it asks for (mock-3's left rail). On a phone the same list folds to a row of
 * numbered markers under "Step 2 of 4" and the current step's title — the titles stay in the
 * accessibility tree, only off the screen. Nothing is rendered twice.
 *
 * **Steps are an ordered list.** The current one carries `aria-current="step"`; the others say
 * whether they are done or not started, and a step with an error says so, in words. Steps already
 * reached are buttons that go back to them; the ones ahead are plain text.
 *
 * Pair it with `StepperActions` for Back / Continue, and keep one `<form>` around both so Enter
 * submits the step.
 */
export function Stepper({
  steps,
  current,
  reached,
  onStepChange,
  label = "Form progress",
  className,
}: StepperProps) {
  const furthest = Math.max(reached ?? current, current);
  const active = steps[current];

  return (
    <nav aria-label={label} className={cn("flex flex-col gap-3", className)}>
      <ol className="flex items-center justify-center gap-3 md:flex-col md:items-stretch md:gap-1">
        {steps.map((step, index) => {
          const status = statusOf(index, current);
          const invalid = step.invalid === true;
          const navigable = onStepChange !== undefined && index !== current && index <= furthest;
          const body = (
            <>
              <Marker index={index} status={status} invalid={invalid} />
              <span className="sr-only flex min-w-0 flex-col gap-0.5 text-left md:not-sr-only">
                <span
                  className={cn(
                    "text-sm font-semibold",
                    status === "current" ? "text-accent-ink" : "text-ink",
                  )}
                >
                  {step.title}
                </span>
                {step.description ? (
                  <span className={cn("text-xs", status === "current" ? "text-ink-soft" : "text-muted")}>
                    {step.description}
                  </span>
                ) : null}
              </span>
              {STATUS_WORD[status] || invalid ? (
                <span className="sr-only">
                  {[STATUS_WORD[status], invalid ? "Has errors" : ""].filter(Boolean).join(", ")}
                </span>
              ) : null}
            </>
          );
          const row = cn(
            "flex items-center gap-3 rounded-tile md:p-3",
            status === "current" && "md:bg-accent-soft",
          );
          return (
            <li key={step.id} aria-current={status === "current" ? "step" : undefined}>
              {navigable ? (
                <button
                  type="button"
                  onClick={() => onStepChange(index)}
                  className={cn(row, "w-full transition-colors md:hover:bg-ground")}
                >
                  {body}
                </button>
              ) : (
                <div className={row}>{body}</div>
              )}
            </li>
          );
        })}
      </ol>
      {active ? (
        <p className="flex flex-col items-center gap-0.5 text-center md:hidden">
          <span className="text-xs font-semibold text-muted">
            Step {current + 1} of {steps.length}
          </span>
          <span className="text-sm font-bold text-ink">{active.title}</span>
        </p>
      ) : null}
    </nav>
  );
}

interface StepperActionsProps {
  /** Omit on the first step: there is nothing to go back to, so no Back button. */
  onBack?: (() => void) | undefined;
  backLabel?: string | undefined;
  /**
   * The forward action. Omit it to make the button the form's submit — the usual shape, so Enter
   * in a field continues and each step is validated in the one `onSubmit`.
   */
  onContinue?: (() => void) | undefined;
  /** "Continue" by default. On the last step, the verb that finishes: "Save product". */
  continueLabel?: string | undefined;
  /** The last step: no arrow on the button, because it goes nowhere after this. */
  final?: boolean | undefined;
  /** From `useMutation`'s `pending`, on the final step. Disables Back as well. */
  pending?: boolean | undefined;
  pendingLabel?: string | undefined;
  className?: string | undefined;
}

/**
 * Back and Continue for a `Stepper` form.
 *
 * On a phone the bar sticks to the bottom of the screen, full-width buttons side by side, so the
 * next step is always one thumb away however long the step is (brief §8–9). From 768px it sits
 * under the form, right-aligned, like any form's actions. Back is secondary and Continue is the
 * one primary action on the screen. Cancel and Save draft belong in the page header (mock-3), not
 * in this bar, where a phone has room for two buttons and no more.
 */
export function StepperActions({
  onBack,
  backLabel = "Back",
  onContinue,
  continueLabel = "Continue",
  final = false,
  pending = false,
  pendingLabel,
  className,
}: StepperActionsProps) {
  return (
    <div
      className={cn(
        "sticky bottom-0 z-10 flex items-center gap-3 border-t border-line bg-surface py-3",
        "md:static md:justify-end md:border-0 md:bg-transparent md:py-0",
        className,
      )}
    >
      {onBack ? (
        <Button
          variant="outline"
          onClick={onBack}
          disabled={pending}
          leadingIcon={<ArrowLeftIcon className="size-4" />}
          className="flex-1 md:flex-none"
        >
          {backLabel}
        </Button>
      ) : null}
      <Button
        type={onContinue ? "button" : "submit"}
        onClick={onContinue}
        loading={pending}
        trailingIcon={final ? undefined : <ArrowRightIcon className="size-4" />}
        className="flex-1 md:flex-none"
      >
        {pending ? (pendingLabel ?? continueLabel) : continueLabel}
      </Button>
    </div>
  );
}
