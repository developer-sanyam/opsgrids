import type { Meta, StoryObj } from "@storybook/react-vite";

import { STATUS_TONES, STATUS_WORDS, statusTone } from "../lib/status";
import type { StatusWord } from "../lib/status";
import { StatusBadge } from "./StatusBadge";

const meta = {
  title: "Data display/StatusBadge",
  component: StatusBadge,
  args: { status: "active" },
} satisfies Meta<typeof StatusBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** The five tones. Closed: a product word is placed on one of these, never on a sixth colour. */
export const Tones: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-2">
      {STATUS_TONES.map((tone) => (
        <StatusBadge key={tone} tone={tone} label={`${tone.charAt(0).toUpperCase()}${tone.slice(1)}`} />
      ))}
    </div>
  ),
};

/** The owner brief's ten (§11), and the four priorities, as the mockups colour them. */
export const BriefVocabulary: Story = {
  render: () => {
    const lifecycle: StatusWord[] = [
      "active",
      "inactive",
      "pending",
      "processing",
      "completed",
      "failed",
      "suspended",
      "trial",
      "cancelled",
      "overdue",
    ];
    const priority: StatusWord[] = ["low", "medium", "high", "urgent"];
    return (
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          {lifecycle.map((word) => (
            <StatusBadge key={word} status={word} />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {priority.map((word) => (
            <StatusBadge key={word} status={word} />
          ))}
        </div>
      </div>
    );
  },
};

/** Every registered word, grouped by the tone the registry gives it. */
export const Registry: Story = {
  render: () => (
    <div className="flex max-w-3xl flex-col gap-4">
      {STATUS_TONES.map((tone) => (
        <section key={tone} className="flex flex-col gap-2">
          <h3 className="text-caption font-semibold text-muted">{tone}</h3>
          <div className="flex flex-wrap gap-2">
            {STATUS_WORDS.filter((word) => statusTone(word) === tone).map((word) => (
              <StatusBadge key={word} status={word} />
            ))}
          </div>
        </section>
      ))}
    </div>
  ),
};

/** A registered word in the product's own wording: the colour still follows the word. */
export const ProductLabel: Story = { args: { status: "past_due", label: "Payment late" } };

/** A word only one product has, placed on a tone by that product. */
export const ProductWord: Story = { args: { tone: "warning", label: "Awaiting DNS" } };

/** In a narrow cell the label truncates on one line rather than wrapping the row taller. */
export const LongLabel: Story = {
  render: () => (
    <div className="w-40 rounded-tile bg-surface p-2 shadow-card">
      <StatusBadge tone="danger" label="Rejected by Meta — template category mismatch" />
    </div>
  ),
};
