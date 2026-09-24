import type { Meta, StoryObj } from "@storybook/react-vite";

import { Card } from "./Card";
import { QrCode } from "./QrCode";

const meta = {
  title: "Data display/QrCode",
  component: QrCode,
  args: {
    value: "https://opsgrids.com/verify/INV-2026-000482",
    label: "Verification link for invoice INV-2026-000482",
    size: 200,
  },
} satisfies Meta<typeof QrCode>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Always dark-on-white, in both themes: scanners do not all read an inverted code. */
export const Default: Story = {};

export const InACard: Story = {
  render: (args) => (
    <Card className="inline-flex flex-col items-center gap-3 p-5">
      <QrCode {...args} size={160} />
      <span className="text-sm text-muted">Scan to verify this invoice</span>
    </Card>
  ),
};

/** More data and the highest error correction: a denser code at the same size. */
export const DenseHighCorrection: Story = {
  args: {
    value: "INV-2026-000482 | Acme Industries | INR 18,40,000.00 | GSTIN 29ABCDE1234F1Z5 | due 30 Sep 2026",
    level: "H",
    label: "Invoice summary for INV-2026-000482",
  },
};
