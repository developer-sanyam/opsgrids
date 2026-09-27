import { useId } from "react";

import { UPDATE_RULES } from "../lib/importExportCopy";
import type { ImportExportMode } from "../lib/importExportView";
import { RadioGroup } from "./RadioGroup";
import type { RadioOption } from "./RadioGroup";

const OPTIONS: RadioOption[] = [
  { value: "add", label: "Add new records only" },
  { value: "addAndUpdate", label: "Add new and update existing" },
];

interface ModeChoiceProps {
  mode: ImportExportMode;
  onChange: (mode: ImportExportMode) => void;
  /**
   * The rules of updating start closed, behind their own heading. On the preview the rows are what
   * the eye needs, and the rules were on screen when the mode was chosen in the menu; they stay one
   * key press away for someone who switches here.
   */
  collapsed?: boolean;
}

/**
 * What an import does with a row that names a record that already exists: leave it (the default,
 * and the only choice a list that cannot be updated has) or change it. The second choice overwrites
 * live data, so its rules sit right under the question, in words, and only once it is chosen.
 *
 * A real `<fieldset>` of two radios: arrow keys move and choose, as the browser does.
 */
export function ImportExportModeChoice({ mode, onChange, collapsed = false }: ModeChoiceProps) {
  const name = useId();
  const rules = (
    <ul aria-label="How updating works" className="flex list-disc flex-col gap-1 pl-4 text-body-sm text-muted">
      {UPDATE_RULES.map((rule) => (
        <li key={rule}>{rule}</li>
      ))}
    </ul>
  );
  return (
    <div className="flex flex-col gap-3">
      <RadioGroup
        legend="What should the import do?"
        name={name}
        value={mode}
        options={OPTIONS}
        onChange={(value) => onChange(value === "addAndUpdate" ? "addAndUpdate" : "add")}
      />
      {mode === "addAndUpdate" ? (
        collapsed ? (
          <details className="flex flex-col gap-2">
            <summary className="w-fit cursor-pointer text-body-sm font-semibold text-ink-soft">How updating works</summary>
            <div className="pt-2">{rules}</div>
          </details>
        ) : (
          rules
        )
      ) : null}
    </div>
  );
}
