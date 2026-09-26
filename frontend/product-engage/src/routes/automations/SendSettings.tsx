import type { GraphNode, MessageChannel, MessagePurpose, TriggerVariable } from "@/api/automations";
import { Button, InlineAlert, Select, Skeleton } from "@/ui";
import type { SelectOption } from "@/ui";
import { withoutKey } from "./graph";
import { PathField } from "./PathField";
import { previewText } from "./preview";
import type { SourceState } from "./useTemplateOptions";

type SendNode = GraphNode & { type: "send_message" };

const CHANNEL_OPTIONS: SelectOption[] = [
  { value: "whatsapp", label: "WhatsApp" },
  { value: "email", label: "Email" },
];

const PURPOSE_OPTIONS: SelectOption[] = [
  { value: "", label: "Let the template decide" },
  { value: "transactional", label: "Transactional" },
  { value: "marketing", label: "Marketing" },
];

export function SendSettings({
  node,
  variables,
  source,
  onReload,
  onChange,
}: {
  node: SendNode;
  variables: TriggerVariable[];
  source: SourceState;
  onReload: (channel: MessageChannel) => void;
  onChange: (node: GraphNode) => void;
}) {
  const { channel, template, recipient_path, variable_map, purpose } = node.config;
  const options = source.status === "ready" ? source.items : [];
  const chosen = options.find((t) => t.ref === template.ref);
  const keys = chosen?.variables ?? [];

  function patch(next: Partial<SendNode["config"]>) {
    onChange({ ...node, config: { ...node.config, ...next } });
  }

  function chooseChannel(next: MessageChannel) {
    if (next === channel) return;
    /* A template belongs to one channel, so switching drops it and the mapping that went with it.
       The recipient path stays: it names where an address is read from, not what is sent. */
    patch({ channel: next, template: { ref: "" }, variable_map: {} });
  }

  function chooseTemplate(ref: string) {
    const picked = options.find((t) => t.ref === ref);
    /* Keep a mapping whose key the new template also has; drop the rest. */
    const kept = Object.fromEntries(
      Object.entries(variable_map).filter(([key]) => picked?.variables.includes(key) === true),
    );
    patch({
      template: picked?.language != null ? { ref, language: picked.language } : { ref },
      variable_map: kept,
    });
  }

  function setPurpose(value: string) {
    const rest = withoutKey(node.config, "purpose");
    onChange({
      ...node,
      config: value === "" ? rest : { ...rest, purpose: value as MessagePurpose },
    });
  }

  const templateOptions: SelectOption[] = [
    { value: "", label: source.status === "ready" ? "Choose a template" : "Loading templates…", disabled: true },
    ...options.map((t) => ({ value: t.ref, label: t.language === null ? t.label : `${t.label} (${t.language})` })),
    ...(template.ref !== "" && chosen === undefined
      ? [{ value: template.ref, label: `${template.ref} (not available)` }]
      : []),
  ];

  return (
    <div className="flex flex-col gap-4">
      <Select
        label="Channel"
        value={channel}
        options={CHANNEL_OPTIONS}
        onChange={(event) => chooseChannel(event.target.value === "email" ? "email" : "whatsapp")}
      />

      {source.status === "error" ? (
        <InlineAlert
          tone="danger"
          title={`Couldn't load ${channel === "email" ? "email" : "WhatsApp"} templates`}
          action={
            <Button variant="secondary" size="sm" onClick={() => onReload(channel)}>
              Try again
            </Button>
          }
        >
          {source.message}
        </InlineAlert>
      ) : null}

      {source.status === "loading" || source.status === "idle" ? (
        <Skeleton className="h-11 w-full rounded-tile" />
      ) : (
        <Select
          label="Template"
          required
          value={template.ref}
          options={templateOptions}
          onChange={(event) => chooseTemplate(event.target.value)}
          {...(source.status === "ready" && options.length === 0
            ? {
                helper:
                  channel === "whatsapp"
                    ? "No approved WhatsApp templates yet. Only approved templates can be sent."
                    : "No email templates yet.",
              }
            : {})}
        />
      )}

      <PathField
        label="Send to"
        required
        value={recipient_path}
        variables={variables}
        placeholder="payload.customer.phone"
        helper={`Where the ${channel === "email" ? "email address" : "phone number"} is read from on the event.`}
        onChange={(value) => patch({ recipient_path: value })}
      />

      <Select
        label="Purpose"
        value={purpose ?? ""}
        options={PURPOSE_OPTIONS}
        onChange={(event) => setPurpose(event.target.value)}
      />

      {keys.length > 0 ? (
        <fieldset className="flex flex-col gap-3">
          <legend className="text-sm font-bold text-ink">Map variables ({keys.length})</legend>
          {keys.map((key) => (
            <PathField
              key={key}
              label={`Value for {{${key}}}`}
              required
              value={variable_map[key] ?? ""}
              variables={variables}
              onChange={(value) => patch({ variable_map: { ...variable_map, [key]: value } })}
            />
          ))}
        </fieldset>
      ) : null}

      {chosen !== undefined && chosen.preview !== "" ? (
        <div className="flex flex-col gap-1">
          <span className="text-sm font-bold text-ink">Message preview</span>
          <p className="rounded-tile bg-sunken p-3 text-sm text-ink" data-testid="message-preview">
            {previewText(chosen.preview, variable_map, variables)}
          </p>
        </div>
      ) : null}
    </div>
  );
}
