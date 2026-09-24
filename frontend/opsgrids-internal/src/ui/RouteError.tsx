import { useRouteError } from "react-router-dom";

import { Button, Card, ErrorState } from "@opsgrids/ui";

/**
 * What a screen shows when it throws while rendering — a shape it was not built for, a bug. It sits
 * inside the shell, so the navigation is still there to leave by, and it says what a person can do:
 * reload. The message is the error's own sentence and never its stack, which is for a developer's
 * console and not an operator's screen.
 *
 * Without it React Router's default takes the whole page over with a developer error screen, or —
 * in a production build — with nothing at all.
 */
export function RouteError() {
  const error = useRouteError();
  const message =
    error instanceof Error && error.message !== ""
      ? error.message
      : "Something in this screen could not be shown.";
  return (
    <Card className="p-6">
      <ErrorState
        title="This screen couldn't be shown"
        message={message}
        action={
          <Button variant="outline" onClick={() => window.location.reload()}>
            Reload the page
          </Button>
        }
      />
    </Card>
  );
}
