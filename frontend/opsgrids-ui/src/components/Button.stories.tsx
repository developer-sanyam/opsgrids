import type { Meta, StoryObj } from "@storybook/react-vite";

import { ArrowRightIcon, PlusIcon } from "../icons";
import { Button } from "./Button";
import type { ButtonVariant } from "./Button";

const VARIANTS: ButtonVariant[] = ["primary", "dark", "outline", "ghost", "danger"];

const meta = {
  title: "Actions/Button",
  component: Button,
  args: { children: "Save changes" },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Primary: Story = {};

/** Every variant at both sizes — the one accent is `primary`; `danger` is only ever destructive. */
export const Variants: Story = {
  render: () => (
    <div className="flex flex-col gap-4">
      {(["md", "sm"] as const).map((size) => (
        <div key={size} className="flex flex-wrap items-center gap-3">
          {VARIANTS.map((variant) => (
            <Button key={variant} variant={variant} size={size}>
              {variant === "danger" ? "Delete" : `${variant[0]?.toUpperCase()}${variant.slice(1)}`}
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
      <Button variant="outline" trailingIcon={<ArrowRightIcon className="size-5" />}>
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
