import { PageHeader } from "@opsgrids/ui";

import { TaskListPanel } from "@/ui/TaskListPanel";

/**
 * The worker's landing screen: their offered and assigned pick/pack tasks.
 *
 * F3's task/offer backend does not exist yet (`docs/fulfilment/plan.md`), so there is nothing to
 * fetch — `status="empty"` is a constant, not a stand-in for fake rows. Once
 * `GET /operations/fulfilment/tasks/mine` (or whatever F3 actually ships) exists, this becomes
 * `useAsync(loadMyTasks, [])` feeding `TaskListPanel`'s `status`/`errorMessage`/`onRetry`, which
 * are already built and tested (`TaskListPanel.test.tsx`).
 */
export function HomePage() {
  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title="Tasks" description="Your offered and assigned pick, pack and dispatch tasks." />
      <TaskListPanel status="empty" />
    </div>
  );
}
