import { useNavigate } from "react-router-dom";

import { Button, Card, ErrorState } from "@/ui";

/**
 * What a screen shows in its own place when it throws while rendering — the shell, the sidebar and
 * every other screen stay up. Used as `errorElement` on the map and floor-view routes, which draw
 * from data the server controls the shape of; without one, a thrown error would bubble to the
 * router's default screen and replace the whole app.
 */
export function RouteError() {
  const navigate = useNavigate();
  return (
    <Card className="p-6">
      <ErrorState
        title="This screen couldn't be shown"
        message="Something went wrong drawing it. The rest of Operations is unaffected."
        action={
          <Button variant="secondary" size="sm" onClick={() => navigate("/warehouses")}>
            Go to warehouses
          </Button>
        }
      />
    </Card>
  );
}
