import { useNavigate } from "react-router-dom";

import { Button, ErrorState } from "@opsgrids/ui";

/** A real not-found state — not the old "Coming soon" eyebrow, which claimed a screen was on its
 *  way when there is simply no page at this address. */
export function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="rise flex min-h-[60vh] flex-col items-center justify-center">
      <ErrorState
        title="That page doesn't exist"
        message="Check the address, or head back to the dashboard."
        tone="neutral"
        action={
          <Button variant="dark" onClick={() => navigate("/")}>
            Back to dashboard
          </Button>
        }
      />
    </div>
  );
}
