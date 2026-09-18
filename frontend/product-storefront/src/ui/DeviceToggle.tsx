import { LaptopIcon, MonitorIcon, PhoneIcon, Pill } from "@opsgrids/ui";

import type { DeviceKey } from "./deviceWidths";

const OPTIONS: { key: DeviceKey; label: string; icon: typeof MonitorIcon }[] = [
  { key: "desktop", label: "Desktop", icon: MonitorIcon },
  { key: "tablet", label: "Tablet", icon: LaptopIcon },
  { key: "mobile", label: "Mobile", icon: PhoneIcon },
];

/**
 * A single-choice group of width presets for the store preview.
 *
 * Each option is a pressed-state pill rather than a radio, because the change takes effect the
 * moment it is chosen — there is nothing to submit — and `aria-pressed` says so.
 */
export function DeviceToggle({
  value,
  onChange,
}: {
  value: DeviceKey;
  onChange: (next: DeviceKey) => void;
}) {
  return (
    <div role="group" aria-label="Preview width" className="flex items-center gap-1">
      {OPTIONS.map((option) => {
        const Icon = option.icon;
        return (
          <Pill
            key={option.key}
            active={value === option.key}
            onClick={() => onChange(option.key)}
            leadingIcon={<Icon className="size-4" />}
          >
            {option.label}
          </Pill>
        );
      })}
    </div>
  );
}
