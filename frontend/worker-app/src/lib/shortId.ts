/**
 * A UUID, shortened for display — the same `slice(0, 8)` convention
 * `product-operations/src/routes/FulfilmentTasks.tsx` already uses for an order id on the
 * dispatcher board.
 *
 * Only a fallback now. The `OPERATIONS_WORKER` role holds neither `operations.orders.order.read`
 * nor `operations.inventory.location.read`, so the server attaches the order number, bin code and
 * item name to a task's own payload when the task is assigned to the caller
 * (`lib/display.ts`). This is what shows when one of those is absent — a task that is not theirs,
 * or a field the server could not resolve.
 */
export function shortId(id: string): string {
  return id.slice(0, 8);
}
