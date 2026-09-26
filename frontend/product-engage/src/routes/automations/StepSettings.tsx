import type { GraphNode, MessageChannel, TriggerOut, TriggerVariable } from "@/api/automations";
import { Button, InlineAlert, Select, Skeleton, TextArea, TextField } from "@/ui";
import type { SelectOption } from "@/ui";
import { ConditionSettings } from "./ConditionSettings";
import { MAX_WAIT_SECONDS, MIN_WAIT_SECONDS, NODE_LABEL, splitDuration, toSeconds, withoutKey } from "./graph";
import type { DurationUnit } from "./graph";
import { SendSettings } from "./SendSettings";
import type { TemplateSources } from "./useTemplateOptions";

/**
 * The settings for whichever step is selected, and nothing for the rest: one panel, one step. The
 * fields are the step's config, changed by handing a whole new node up. A `fieldset` carries
 * `disabled` for a published automation, which locks every control inside it at once — the
 * server refuses the edit anyway, and this says so before anyone types.
 */

export interface TriggerCatalogue {
  status: "loading" | "ready" | "error";
  items: TriggerOut[];
  error: string | null;
  reload: () => void;
}

const UNIT_OPTIONS: SelectOption[] = [
  { value: "minutes", label: "Minutes" },
  { value: "hours", label: "Hours" },
  { value: "days", label: "Days" },
];

const DEFAULT_WINDOW = { start: "09:00", end: "21:00", timezone: "Asia/Kolkata" };

type Node<T extends GraphNode["type"]> = GraphNode & { type: T };

function TriggerSettings({
  node,
  catalogue,
  onChange,
}: {
  node: Node<"trigger">;
  catalogue: TriggerCatalogue;
  onChange: (node: GraphNode) => void;
}) {
  if (catalogue.status === "loading") return <Skeleton className="h-11 w-full rounded-tile" />;
  if (catalogue.status === "error") {
    return (
      <InlineAlert
        tone="danger"
        title="Couldn't load the events you can start from"
        action={
          <Button variant="secondary" size="sm" onClick={catalogue.reload}>
            Try again
          </Button>
        }
      >
        {catalogue.error ?? "Try again in a moment."}
      </InlineAlert>
    );
  }
  const current = node.config.event_type;
  const known = catalogue.items.some((t) => t.event_type === current);
  const options: SelectOption[] = [
    { value: "", label: "Choose an event", disabled: true },
    ...catalogue.items.map((t) => ({ value: t.event_type, label: `${t.label} (${t.module})` })),
    ...(current !== "" && !known ? [{ value: current, label: `${current} (not available)` }] : []),
  ];
  const picked = catalogue.items.find((t) => t.event_type === current);
  return (
    <div className="flex flex-col gap-4">
      <Select
        label="Event"
        required
        value={current}
        options={options}
        helper={picked === undefined ? "Runs once for each event, as it happens." : `Event: ${picked.event_type}. Runs once for each event.`}
        onChange={(event) => onChange({ ...node, config: { event_type: event.target.value } })}
      />
      {picked !== undefined && picked.variables.length > 0 ? (
        <div className="flex flex-col gap-2">
          <span className="text-sm font-bold text-ink">Available variables</span>
          <ul className="flex flex-wrap gap-2">
            {picked.variables.map((v) => (
              <li key={v.path} className="rounded-chip bg-sunken px-2 py-1 text-xs text-ink" title={v.path}>
                {v.label}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function WaitSettings({ node, onChange }: { node: Node<"wait">; onChange: (node: GraphNode) => void }) {
  const { seconds, window } = node.config;
  const { value, unit } = splitDuration(seconds);
  const outOfRange = seconds < MIN_WAIT_SECONDS || seconds > MAX_WAIT_SECONDS;

  function setDuration(nextValue: number, nextUnit: DurationUnit) {
    onChange({ ...node, config: { ...node.config, seconds: toSeconds(nextValue, nextUnit) } });
  }

  function setWindow(enabled: boolean) {
    const rest = withoutKey(node.config, "window");
    onChange({ ...node, config: enabled ? { ...rest, window: DEFAULT_WINDOW } : rest });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3">
        <TextField
          label="Wait for"
          type="number"
          min={1}
          value={String(value)}
          {...(outOfRange ? { error: "Wait between 1 minute and 30 days." } : {})}
          onChange={(event) => setDuration(Number(event.target.value), unit)}
        />
        <Select
          label="Unit"
          value={unit}
          options={UNIT_OPTIONS}
          onChange={(event) => setDuration(value, event.target.value as DurationUnit)}
        />
      </div>

      <label className="flex items-start gap-3 text-sm text-ink">
        <input
          type="checkbox"
          className="mt-1 size-4 accent-accent"
          checked={window !== undefined}
          onChange={(event) => setWindow(event.target.checked)}
        />
        <span>
          <span className="font-semibold">Only continue during a window</span>
          <span className="block text-muted">
            After the wait, hold the next step until the window opens. Times are India Standard Time.
          </span>
        </span>
      </label>

      {window !== undefined ? (
        <div className="grid grid-cols-2 gap-3">
          <TextField
            label="Window opens"
            type="time"
            value={window.start}
            onChange={(event) => onChange({ ...node, config: { ...node.config, window: { ...window, start: event.target.value } } })}
          />
          <TextField
            label="Window closes"
            type="time"
            value={window.end}
            onChange={(event) => onChange({ ...node, config: { ...node.config, window: { ...window, end: event.target.value } } })}
          />
        </div>
      ) : null}
    </div>
  );
}

function TaskSettings({ node, onChange }: { node: Node<"create_task">; onChange: (node: GraphNode) => void }) {
  const { title, description, assignee_team } = node.config;

  function patch(field: "title" | "description" | "assignee_team", value: string) {
    const config = field === "title" || value !== "" ? { ...node.config, [field]: value } : withoutKey(node.config, field);
    onChange({ ...node, config });
  }

  return (
    <div className="flex flex-col gap-4">
      <TextField label="Task title" required value={title} onChange={(event) => patch("title", event.target.value)} />
      <TextArea
        label="Description"
        optional
        rows={3}
        value={description ?? ""}
        onChange={(event) => patch("description", event.target.value)}
      />
      <TextField
        label="Assign to team"
        optional
        value={assignee_team ?? ""}
        onChange={(event) => patch("assignee_team", event.target.value)}
      />
    </div>
  );
}

export function StepSettings({
  node,
  readOnly,
  triggers,
  variables,
  templates,
  onChange,
}: {
  node: GraphNode;
  readOnly: boolean;
  triggers: TriggerCatalogue;
  variables: TriggerVariable[];
  templates: TemplateSources;
  onChange: (node: GraphNode) => void;
}) {
  return (
    <aside aria-label="Step settings" className="flex flex-col gap-4">
      <h2 className="text-lg font-bold">{NODE_LABEL[node.type]} settings</h2>
      <fieldset disabled={readOnly} className="flex min-w-0 flex-col gap-4">
        <legend className="sr-only">{NODE_LABEL[node.type]} step</legend>
        {node.type === "trigger" ? <TriggerSettings node={node} catalogue={triggers} onChange={onChange} /> : null}
        {node.type === "condition" ? <ConditionSettings node={node} variables={variables} onChange={onChange} /> : null}
        {node.type === "wait" ? <WaitSettings node={node} onChange={onChange} /> : null}
        {node.type === "send_message" ? (
          <SendSettings
            node={node}
            variables={variables}
            source={templates.sources[node.config.channel]}
            onReload={(channel: MessageChannel) => templates.reload(channel)}
            onChange={onChange}
          />
        ) : null}
        {node.type === "create_task" ? <TaskSettings node={node} onChange={onChange} /> : null}
        {node.type === "end" ? <p className="text-sm text-muted">This path ends here.</p> : null}
      </fieldset>
      {readOnly ? (
        <p className="text-sm text-muted">Published automations can't be edited, so these settings are read only.</p>
      ) : null}
    </aside>
  );
}
