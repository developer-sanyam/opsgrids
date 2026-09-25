import type { Meta, StoryObj } from "@storybook/react-vite";

import { Card } from "./Card";
import { FormSection } from "./FormSection";
import { Select } from "./Select";
import { TextArea } from "./TextArea";
import { TextField } from "./TextField";

const CATEGORIES = [
  { value: "bags", label: "Bags" },
  { value: "stationery", label: "Stationery" },
  { value: "electronics", label: "Electronics" },
];

const meta = {
  title: "Forms/FormSection",
  component: FormSection,
  args: {
    title: "Basic information",
    description: "Add the main details about your product.",
    children: null,
  },
  render: (args) => (
    <Card className="max-w-4xl p-4 md:p-6">
      <FormSection {...args} />
    </Card>
  ),
} satisfies Meta<typeof FormSection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Two columns from 768px, one below. Required fields carry the red asterisk; optional ones say so. */
export const TwoColumns: Story = {
  args: {
    children: (
      <>
        <TextField label="Product name" required defaultValue="Canvas Laptop Bag" />
        <Select label="Category" required defaultValue="bags" options={CATEGORIES} />
        <TextArea
          label="Short description"
          required
          rows={2}
          maxLength={300}
          counterFrom={0}
          value="Stylish and durable laptop bag for everyday use."
          onChange={() => undefined}
          containerClassName="col-span-full"
        />
        <TextField label="Barcode" optional inputMode="numeric" defaultValue="123456789012" />
        <TextField label="SKU" required defaultValue="CB-001" />
      </>
    ),
  },
};

/** Three columns at 1280px (two on a tablet): short numeric fields in a row. */
export const ThreeColumns: Story = {
  args: {
    title: "Dimensions",
    description: "Packed size, used for shipping rates.",
    columns: 3,
    children: (
      <>
        <TextField label="Length (cm)" inputMode="decimal" defaultValue="42" />
        <TextField label="Width (cm)" inputMode="decimal" defaultValue="30" />
        <TextField label="Height (cm)" inputMode="decimal" defaultValue="10" />
      </>
    ),
  },
};

export const OneColumn: Story = {
  args: {
    title: "Notes",
    description: undefined,
    columns: 1,
    children: <TextArea label="Internal note" optional rows={3} />,
  },
};

/** A 422 lands on each field that caused it. */
export const WithErrors: Story = {
  args: {
    title: "Pricing & stock",
    description: "What it costs and how many you have.",
    children: (
      <>
        <TextField label="Selling price" required inputMode="decimal" defaultValue="4,980" />
        <TextField label="Cost price" inputMode="decimal" defaultValue="5,250" error="Cost price is higher than the selling price." />
        <TextField label="Initial stock" required inputMode="numeric" defaultValue="" error="Enter the stock you have on hand, or 0." />
        <TextField label="Low stock alert" optional inputMode="numeric" defaultValue="10" />
      </>
    ),
  },
};
