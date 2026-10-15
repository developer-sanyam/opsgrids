import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Pagination } from "./Pagination";

function Stateful(props: ComponentProps<typeof Pagination>) {
  const [page, setPage] = useState(props.page);
  const [size, setSize] = useState(props.size);
  const pages = props.total === 0 ? 0 : Math.ceil(props.total / size);
  return (
    <Pagination
      {...props}
      page={Math.min(page, Math.max(pages, 1))}
      size={size}
      pages={pages}
      onPageChange={(next) => {
        setPage(next);
        props.onPageChange(next);
      }}
      {...(props.onSizeChange
        ? {
            onSizeChange: (next: number) => {
              setSize(next);
              setPage(1);
              props.onSizeChange?.(next);
            },
          }
        : {})}
    />
  );
}

const meta = {
  title: "Navigation/Pagination",
  component: Pagination,
  args: { page: 1, pages: 5, total: 112, size: 25, noun: "contacts", onPageChange: fn() },
  render: (args) => <Stateful {...args} />,
} satisfies Meta<typeof Pagination>;

export default meta;
type Story = StoryObj<typeof meta>;

export const FirstPage: Story = {};

/** Deep in a long list: both ends one click away, ellipses between. */
export const MiddleOfMany: Story = { args: { page: 18, pages: 40, total: 998 } };

export const LastPage: Story = { args: { page: 5 } };

export const WithPageSize: Story = { args: { onSizeChange: fn() } };

/** One page: the count line only — no controls that go nowhere. */
export const SinglePage: Story = { args: { pages: 1, total: 7 } };

export const Empty: Story = { args: { page: 1, pages: 0, total: 0 } };

/** Exactly one row: the singular noun, so it never reads "1 contacts". */
export const SingleRow: Story = { args: { pages: 1, total: 1, singularNoun: "contact" } };
