/**
 * The two non-happy states every gated Home widget can land on (dashboard audit §5.0.8), built
 * from `@opsgrids/ui`'s existing `EmptyState` and `Card` rather than a new design-system
 * component — the copy is the only thing either state needs that a generic empty state doesn't
 * already carry.
 *
 * `compact` draws the KPI row's shape (icon tile, two lines, ~140px tall) so a gated card never
 * throws off its row's height; the full-size form is for a chart, a table or a list's own empty
 * slot.
 */
import { Button, Card, EmptyState, PlugIcon, ShieldIcon } from "@opsgrids/ui";

interface PlanGatedNoticeProps {
  /** "Finance", "CRM", "Projects" — the product's display name, named in the sentence. */
  product: string;
  /**
   * Holds `core.product.activation.write:ORG` — only an owner gets the button. Everyone else
   * reads the same sentence with no control that would only 403 (ADR-0003 §3: an absent control
   * is honest, a disabled one is a puzzle).
   */
  canActivate: boolean;
  onStartTrial: () => void;
  compact?: boolean | undefined;
}

/** "{Product} isn't active" — shown instead of fetching data the org's plan doesn't cover. */
export function PlanGatedNotice({ product, canActivate, onStartTrial, compact = false }: PlanGatedNoticeProps) {
  const title = `${product} isn't active for your organisation.`;
  const description = canActivate
    ? "Start a 14-day trial to see this on your dashboard."
    : `Ask your organisation's owner to start ${product}.`;

  if (compact) {
    return (
      <Card className="flex h-full flex-col gap-3 p-4 sm:p-6">
        <span aria-hidden className="grid size-10 place-items-center rounded-tile bg-sunken text-ink-soft">
          <PlugIcon className="size-5" />
        </span>
        <div className="flex flex-col gap-1">
          <p className="text-sm font-semibold text-ink-soft">{title}</p>
          <p className="text-xs text-muted">{description}</p>
        </div>
        {canActivate ? (
          <Button variant="secondary" size="sm" onClick={onStartTrial}>
            Start 14-day trial
          </Button>
        ) : null}
      </Card>
    );
  }

  return (
    <EmptyState
      icon={<PlugIcon className="size-6" />}
      title={title}
      description={description}
      action={canActivate ? <Button variant="secondary" onClick={onStartTrial}>Start 14-day trial</Button> : undefined}
    />
  );
}

interface NoAccessNoticeProps {
  /** "your invoices", "your pipeline" — completes "Ask your organisation's owner for access to …" */
  capability: string;
  compact?: boolean | undefined;
}

/** "No access" — the widget is entitled but the caller holds no grant for it. Never fetched. */
export function NoAccessNotice({ capability, compact = false }: NoAccessNoticeProps) {
  const description = `Ask your organisation's owner for access to ${capability}.`;

  if (compact) {
    return (
      <Card className="flex h-full flex-col gap-3 p-4 sm:p-6">
        <span aria-hidden className="grid size-10 place-items-center rounded-tile bg-sunken text-ink-soft">
          <ShieldIcon className="size-5" />
        </span>
        <div className="flex flex-col gap-1">
          <p className="text-sm font-semibold text-ink-soft">No access</p>
          <p className="text-xs text-muted">{description}</p>
        </div>
      </Card>
    );
  }

  return <EmptyState icon={<ShieldIcon className="size-6" />} title="No access" description={description} />;
}
