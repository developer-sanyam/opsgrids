import type { Meta, StoryObj } from "@storybook/react-vite";

import { ArrowRightIcon, PlusIcon } from "../icons";
import { Button } from "./Button";
import type { ButtonVariant } from "./Button";

/** The brief's hierarchy, in order of weight. `outline` and `danger` are deprecated aliases. */
const VARIANTS: ButtonVariant[] = ["primary", "secondary", "ghost", "destructive", "dark"];

const meta = {
  title: "Actions/Button",
  component: Button,
  args: { children: "Save changes" },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Primary: Story = {};

/** Every variant at both sizes — the one accent is `primary`; `destructive` is only ever that. */
export const Variants: Story = {
  render: () => (
    <div className="flex flex-col gap-4">
      {(["md", "sm"] as const).map((size) => (
        <div key={size} className="flex flex-wrap items-center gap-3">
          {VARIANTS.map((variant) => (
            <Button key={variant} variant={variant} size={size}>
              {variant === "destructive" ? "Delete" : `${variant[0]?.toUpperCase()}${variant.slice(1)}`}
            </Button>
          ))}
        </div>
      ))}
    </div>
  ),
};

export const WithIcons: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button leadingIcon={<PlusIcon className="size-5" />}>New deal</Button>
      <Button variant="secondary" trailingIcon={<ArrowRightIcon className="size-5" />}>
        Continue
      </Button>
      <Button variant="ghost" size="sm" leadingIcon={<PlusIcon className="size-4" />}>
        Add row
      </Button>
    </div>
  ),
};

/** The sixth state: the label keeps its width under the spinner, so nothing shifts mid-submit. */
export const Loading: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      {VARIANTS.map((variant) => (
        <Button key={variant} variant={variant} loading>
          Saving…
        </Button>
      ))}
    </div>
  ),
};

/** The brief's pairing on a form: one primary, its secondary beside it. */
export const Hierarchy: Story = {
  render: () => (
    <div className="flex flex-wrap items-center justify-end gap-3">
      <Button variant="secondary">Cancel</Button>
      <Button>Save product</Button>
    </div>
  ),
};

/** Reached by keyboard: the focus ring is the accent, 2px, offset from the control. */
export const Focused: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button variant="secondary">Cancel</Button>
      <Button>Save product</Button>
    </div>
  ),
  play: async ({ userEvent }) => {
    await userEvent.tab();
  },
};

export const Disabled: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      {VARIANTS.map((variant) => (
        <Button key={variant} variant={variant} disabled>
          Unavailable
        </Button>
      ))}
    </div>
  ),
};

export const LongLabel: Story = {
  args: {
    children: "Send the quotation to every contact at this company and log the activity",
  },
};
