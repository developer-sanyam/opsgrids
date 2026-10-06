import type { ReactNode } from "react";

import { Dialog } from "@opsgrids/ui";
import { Button } from "@/ui";

interface StatusDialogProps {
  title: string;
  description?: string | undefined;
  onClose: () => void;
  children: ReactNode;
}

/**
 * The dialog a stock operation shows while it cannot yet be a form: its stock is still loading,
 * the load failed, or there is nothing it could act on. Same title and Close as the form that
 * replaces it, so the surface does not change under the person's focus — and no submit button
 * that would do nothing.
 */
export function StatusDialog({ title, description, onClose, children }: StatusDialogProps) {
  return (
    <Dialog
      open
      onClose={onClose}
      title={title}
      {...(description !== undefined ? { description } : {})}
      footer={
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div aria-live="polite">{children}</div>
    </Dialog>
  );
}
