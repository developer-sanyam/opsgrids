import type { ReactNode } from "react";

import { ArrowUpRight, Button, Card, PageHeader } from "@opsgrids/ui";

interface LinkOutPageProps {
  icon?: ReactNode;
  eyebrow: string;
  title: string;
  description: string;
  href: string;
  linkLabel: string;
}

/**
 * Departments, Locations and Team Members are administered in the dashboard's admin console now
 * (`opsgrids-admin`), not rebuilt here — "an app may not fork a component into itself" extends to
 * not re-implementing another app's CRUD screens (`frontend/CLAUDE.md`). This page is the honest
 * stand-in: it says where the real screen lives and links straight to it, rather than pretending
 * to be that screen or silently disappearing from the nav.
 */
export function LinkOutPage({ icon, eyebrow, title, description, href, linkLabel }: LinkOutPageProps) {
  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow={eyebrow} title={title} />
      <Card className="flex flex-col items-start gap-4 p-8">
        {icon ? (
          <span className="grid size-12 place-items-center rounded-full bg-ground text-ink-soft">
            {icon}
          </span>
        ) : null}
        <p className="max-w-md text-sm text-muted">{description}</p>
        <Button
          trailingIcon={<ArrowUpRight className="size-4" />}
          onClick={() => window.open(href, "_blank", "noopener,noreferrer")}
        >
          {linkLabel}
        </Button>
      </Card>
    </div>
  );
}
