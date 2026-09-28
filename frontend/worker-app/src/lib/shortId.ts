/**
 * A UUID, shortened for display — the same `slice(0, 8)` convention
 * `product-operations/src/routes/FulfilmentTasks.tsx` already uses for an order id on the
 * dispatcher board.
 *
 * This app shows a shortened id for order, warehouse and location references because that is all
 * the data it has: `PickTaskLineOut` carries only location *ids*, and the `OPERATIONS_WORKER` role
 * holds neither `operations.orders.order.read` nor `operations.inventory.location.read`
 * (`app/catalogue.py`), so there is no order number, no location code and no location barcode
 * this app can resolve them to. See this app's `README.md` for the gap.
 */
export function shortId(id: string): string {
  return id.slice(0, 8);
}
