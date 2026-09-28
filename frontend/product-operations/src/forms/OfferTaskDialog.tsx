import { useState } from "react";

import { listWorkers, offerTask } from "@/api/client";
import type { AssignmentOfferOut, PickTaskOut } from "@/api/types";
import { useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select } from "@/ui";

interface OfferTaskDialogProps {
  task: PickTaskOut;
  onClose: () => void;
  onOffered: (offer: AssignmentOfferOut) => void;
}

/**
 * `POST /operations/fulfilment/tasks/{id}/offers` — `operations.fulfilment.task.assign:ORG`, or
 * `operations.fulfilment.task.override:ORG` when the task already has a live offer (auto-assigned
 * or manual): every offer, whether it's the task's first, a manager's hand-pick after
 * `ESCALATED`, or an override of a live one, goes through this one dialog, the same one endpoint.
 */
export function OfferTaskDialog({ task, onClose, onOffered }: OfferTaskDialogProps) {
  const [workerId, setWorkerId] = useState("");
  const [workerIdError, setWorkerIdError] = useState<string | undefined>(undefined);
  const overriding = task.status === "OFFERED";

  const workersState = useAsync(
    () => listWorkers({ warehouse_id: task.warehouse_id, is_active: true, size: 200 }),
    [task.warehouse_id],
  );
  const options =
    workersState.status === "success"
      ? workersState.data.items.map((w) => ({ value: w.id, label: w.display_name || w.employee_id }))
      : [];

  const offer = useMutation((input: { worker_id: string }) => offerTask(task.id, input), {
    onSuccess: (saved) => onOffered(saved),
  });

  function submit() {
    setWorkerIdError(undefined);
    if (workerId.trim().length < 1) {
      setWorkerIdError("Choose who to offer this task to.");
      return;
    }
    void offer.mutate({ worker_id: workerId });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={overriding ? "Reassign this task to a different worker" : "Offer this task to a worker"}
      description={
        overriding
          ? "This withdraws the current offer and sends a new one instead."
          : `Round ${task.round + 1} of ${task.max_rounds}.`
      }
      submitLabel={overriding ? "Reassign" : "Send offer"}
      pendingLabel="Sending…"
      pending={offer.pending}
      {...formDialogError(offer.error, offer.fieldErrors)}
    >
      <Select
        label="Worker"
        value={workerId}
        onChange={(e) => setWorkerId(e.target.value)}
        options={options}
        placeholder={workersState.status === "loading" ? "Loading workers…" : "Choose a worker"}
        required
        disabled={workersState.status !== "success" || options.length === 0}
        {...(workerIdError !== undefined ? { error: workerIdError } : {})}
      />
      {workersState.status === "success" && options.length === 0 ? (
        <p className="text-sm text-muted">
          No active workers are assigned to this task&apos;s warehouse yet — add one on the Workers page first.
        </p>
      ) : null}
    </FormDialog>
  );
}
