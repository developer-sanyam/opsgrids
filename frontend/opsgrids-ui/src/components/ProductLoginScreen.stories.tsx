import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { PasswordField } from "./PasswordField";
import { ProductLoginScreen } from "./ProductLoginScreen";

const meta = {
  title: "Pages/ProductLoginScreen",
  component: ProductLoginScreen,
  subcomponents: { PasswordField },
  parameters: { layout: "fullscreen" },
  args: {
    brand: "OpsGrids Finance",
    logo: <span className="grid size-9 place-items-center rounded-tile bg-accent text-sm font-bold text-on-accent">OG</span>,
    tagline: "Invoices, payments and books that reconcile themselves.",
    step: "credentials",
    email: "",
    password: "",
    submitting: false,
    error: null,
    onEmailChange: fn(),
    onPasswordChange: fn(),
    onSubmit: fn(),
    onPickOrganization: fn(),
  },
} satisfies Meta<typeof ProductLoginScreen>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Filled: Story = {
  args: { email: "admin@opsgrids-demo.example.com", password: "not-a-real-password" },
};

export const WithDemoAccess: Story = {
  args: { demo: { email: "admin@opsgrids-demo.example.com", onFill: fn() } },
};

export const Submitting: Story = {
  args: { email: "admin@opsgrids-demo.example.com", password: "not-a-real-password", submitting: true },
};

export const WrongCredentials: Story = {
  args: { email: "admin@opsgrids-demo.example.com", error: "That email or password is incorrect." },
};

export const ChooseOrganization: Story = {
  args: {
    step: "organizations",
    organizations: [
      { organization_id: "org-a", name: "Acme India", roles: ["finance_admin", "billing"] },
      { organization_id: "org-b", name: "Acme United Kingdom", roles: ["finance_viewer"] },
    ],
  },
};

export const ChoosingAPassword: Story = {
  render: () => (
    <div className="max-w-sm p-6">
      <PasswordField
        label="New password"
        autoComplete="new-password"
        value="correct horse battery"
        onChange={fn()}
        helper="At least 12 characters."
      />
    </div>
  ),
};

export const PasswordRefused: Story = {
  render: () => (
    <div className="max-w-sm p-6">
      <PasswordField value="short" onChange={fn()} error="That password is too short." />
    </div>
  ),
};

export const ChooseOrganizationFailed: Story = {
  args: {
    ...ChooseOrganization.args,
    error: "Couldn't finish signing in. The gateway timed out.",
  },
};
