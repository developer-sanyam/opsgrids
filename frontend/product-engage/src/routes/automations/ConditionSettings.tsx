import type { ConditionOp, ConditionRule, GraphNode, TriggerVariable } from "@/api/automations";
import { Button, Select, TextField } from "@/ui";
import type { SelectOption } from "@/ui";
import { parseValue, valueText } from "./condition";
import { OP_LABEL } from "./graph";
import { PathField } from "./PathField";

type ConditionNode = GraphNode & { type: "condition" };

const OP_OPTIONS: SelectOption[] = (Object.keys(OP_LABEL) as ConditionOp[]).map((op) => ({
  value: op,
  label: OP_LABEL[op],
}));

const MATCH_OPTIONS: SelectOption[] = [
  { value: "all", label: "All of the rules" },
  { value: "any", label: "Any of the rules" },
];

interface RulePatch {
  path?: string;
  op?: ConditionOp;
  value?: ConditionRule["value"] | undefined;
}

function withRule(rule: ConditionRule, patch: RulePatch): ConditionRule {
  const next: ConditionRule = { path: patch.path ?? rule.path, op: patch.op ?? rule.op };
  const value = "value" in patch ? patch.value : rule.value;
  if (next.op !== "exists" && value !== undefined) next.value = value;
  return next;
}

export function ConditionSettings({
  node,
  variables,
  onChange,
}: {
  node: ConditionNode;
  variables: TriggerVariable[];
  onChange: (node: GraphNode) => void;
}) {
  const { match, rules } = node.config;

  function setRules(next: ConditionRule[]) {
    onChange({ ...node, config: { ...node.config, rules: next } });
  }

  return (
    <div className="flex flex-col gap-4">
      <Select
        label="Continue on yes when"
        value={match}
        options={MATCH_OPTIONS}
        onChange={(event) =>
          onChange({ ...node, config: { ...node.config, match: event.target.value === "any" ? "any" : "all" } })
        }
      />

      <ol className="flex flex-col gap-4">
        {rules.map((rule, index) => (
          <li key={index} className="flex flex-col gap-3 rounded-tile bg-sunken p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-bold text-ink">Rule {index + 1}</span>
              <Button
                variant="ghost"
                size="sm"
                className="text-negative"
                aria-label={`Remove rule ${index + 1}`}
                onClick={() => setRules(rules.filter((_, i) => i !== index))}
              >
                Remove
              </Button>
            </div>
            <PathField
              label={`Field for rule ${index + 1}`}
              value={rule.path}
              variables={variables}
              placeholder="payload.total"
              onChange={(path) => setRules(rules.map((r, i) => (i === index ? withRule(r, { path }) : r)))}
            />
            <Select
              label={`Operator for rule ${index + 1}`}
              value={rule.op}
              options={OP_OPTIONS}
              onChange={(event) => {
                const op = event.target.value as ConditionOp;
                setRules(
                  rules.map((r, i) =>
                    i === index ? withRule(r, { op, value: parseValue(op, valueText(r)) }) : r,
                  ),
                );
              }}
            />
            {rule.op === "exists" ? null : (
              <TextField
                label={`Value for rule ${index + 1}`}
                value={valueText(rule)}
                {...(rule.op === "in" ? { helper: "Separate values with commas." } : {})}
                onChange={(event) =>
                  setRules(
                    rules.map((r, i) =>
                      i === index ? withRule(r, { value: parseValue(r.op, event.target.value) }) : r,
                    ),
                  )
                }
              />
            )}
          </li>
        ))}
      </ol>

      <Button
        variant="secondary"
        size="sm"
        className="self-start"
        onClick={() => setRules([...rules, { path: "", op: "eq" }])}
      >
        Add rule
      </Button>
    </div>
  );
}
