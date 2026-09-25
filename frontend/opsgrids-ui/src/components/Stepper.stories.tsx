import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Card } from "./Card";
import { FileUpload } from "./FileUpload";
import { FormSection } from "./FormSection";
import { Select } from "./Select";
import { Stepper, StepperActions } from "./Stepper";
import type { StepItem } from "./Stepper";
import { TextArea } from "./TextArea";
import { TextField } from "./TextField";

/** The Add Product steps (mock-3). */
const STEPS: StepItem[] = [
  { id: "basic", title: "Basic information", description: "Name, description, category" },
  { id: "pricing", title: "Pricing & stock", description: "Price, stock, SKU" },
  { id: "media", title: "Media", description: "Images and documents" },
  { id: "details", title: "Additional details", description: "SKU, barcode, tax, etc." },
];

const meta = {
  title: "Forms/Stepper",
  component: Stepper,
  subcomponents: { StepperActions },
  args: { steps: STEPS, current: 0 },
  render: (args) => (
    <Card className="max-w-xs p-3">
      <Stepper {...args} />
    </Card>
  ),
} satisfies Meta<typeof Stepper>;

export default meta;
type Story = StoryObj<typeof meta>;

export const FirstStep: Story = {};

/** Step 2, having been to step 3 before: steps 1 and 3 are links back, step 4 is not yet offered. */
export const MiddleStep: Story = { args: { current: 1, reached: 2, onStepChange: fn() } };

/** A step left with errors says so in the list, so it can be found again. */
export const StepWithErrors: Story = {
  args: {
    current: 2,
    onStepChange: fn(),
    steps: STEPS.map((step, index) => (index === 1 ? { ...step, invalid: true } : step)),
  },
};

export const LastStep: Story = { args: { current: 3, onStepChange: fn() } };

/** The bar under a step: sticky at a phone's bottom edge, Back and Continue side by side. */
export const Actions: Story = {
  render: () => (
    <Card className="max-w-2xl p-4">
      <StepperActions onBack={fn()} />
    </Card>
  ),
};

/** The final step saving: both buttons held, the primary saying what it is doing. */
export const ActionsSaving: Story = {
  render: () => (
    <Card className="max-w-2xl p-4">
      <StepperActions onBack={fn()} final continueLabel="Save product" pending pendingLabel="Saving…" />
    </Card>
  ),
};

const CATEGORIES = [
  { value: "bags", label: "Bags" },
  { value: "stationery", label: "Stationery" },
];

/**
 * The whole of mock-3's first step, composed from the primitives: the step rail beside the form
 * from 768px, "Step 1 of 4" and a sticky Back / Continue on a phone.
 */
function AddProduct() {
  const [current, setCurrent] = useState(0);
  const [description, setDescription] = useState("Stylish and durable laptop bag for everyday use.");
  return (
    <form
      noValidate
      className="grid max-w-5xl grid-cols-1 gap-4 md:grid-cols-3"
      onSubmit={(event) => {
        event.preventDefault();
        setCurrent((step) => Math.min(step + 1, STEPS.length - 1));
      }}
    >
      <Card className="p-3 md:self-start">
        <Stepper steps={STEPS} current={current} onStepChange={setCurrent} />
      </Card>
      <Card className="flex flex-col gap-6 p-4 md:col-span-2 md:p-6">
        <FormSection title="Basic information" description="Add the main details about your product." headingLevel={2}>
          <TextField label="Product name" required defaultValue="Canvas Laptop Bag" />
          <Select label="Category" required defaultValue="bags" options={CATEGORIES} />
          <TextArea
            label="Short description"
            required
            rows={2}
            maxLength={300}
            counterFrom={0}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            containerClassName="col-span-full"
          />
          <FileUpload
            label="Product images"
            accept={["image/png", "image/jpeg"]}
            maxSize={5 * 1024 * 1024}
            maxFiles={6}
            onUpload={async (file) => file.name}
            className="col-span-full"
          />
        </FormSection>
        <StepperActions {...(current > 0 ? { onBack: () => setCurrent(current - 1) } : {})} />
      </Card>
    </form>
  );
}

export const AddProductForm: Story = { render: () => <AddProduct /> };
