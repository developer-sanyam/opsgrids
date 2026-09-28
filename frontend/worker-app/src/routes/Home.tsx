import { Card, EmptyState, ErrorState, PageHeader, UserIcon } from "@opsgrids/ui";
import { problemMessage } from "@opsgrids/client";

import { TaskListPanel } from "@/ui/TaskListPanel";
import { useMyWorker } from "@/worker/context";
import { useMyTasks } from "@/worker/useMyTasks";

/**
 * The worker's landing screen: their offered and assigned pick/pack tasks.
 *
 * Two lookups happen before there is anything to show: who this account's `workers` row is
 * (`useMyWorker`), then that worker's own live tasks (`useMyTasks`). Both get their own honest
 * state rather than folding one loading spinner over two different failures — `no-identity` and
 * `not-found` are not "still loading" and not "something broke"; they are facts a supervisor has
 * to fix, and the copy says that.
 */
export function HomePage() {
  const worker = useMyWorker();

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title="Tasks" description="Your offered and assigned pick, pack and dispatch tasks." />
      {worker.status === "no-identity" ? (
        <Card className="p-6">
          <EmptyState
            icon={<UserIcon className="size-6" aria-hidden />}
            title="This account has no worker record"
            description="Ask your supervisor to link your account to a worker profile before you can be offered tasks."
          />
        </Card>
      ) : worker.status === "not-found" ? (
        <Card className="p-6">
          <EmptyState
            icon={<UserIcon className="size-6" aria-hidden />}
            title="You're not on this warehouse's roster yet"
            description="Your account isn't linked to a worker profile in this organization. Ask your supervisor to add one."
          />
        </Card>
      ) : worker.status === "error" ? (
        <Card className="p-2">
          <ErrorState
            title="Couldn't check your worker profile"
            message={worker.error ? problemMessage(worker.error) : "Something went wrong. Try again."}
            onRetry={worker.reload}
          />
        </Card>
      ) : (
        <MyTasks workerId={worker.status === "ready" ? (worker.worker?.id ?? null) : null} />
      )}
    </div>
  );
}

function MyTasks({ workerId }: { workerId: string | null }) {
  const tasksState = useMyTasks(workerId);

  if (workerId === null || tasksState.status === "loading") return <TaskListPanel status="loading" />;
  if (tasksState.status === "error") {
    return (
      <TaskListPanel status="error" errorMessage={problemMessage(tasksState.error)} onRetry={tasksState.reload} />
    );
  }
  return <TaskListPanel status="populated" tasks={tasksState.data} />;
}
