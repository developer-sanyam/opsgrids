import type { MutationOptions } from "@/api/client";
import type { ProductionActionOut } from "@/api/types";
import { useIdempotencyKeys } from "@/lib/useIdempotencyKeys";
import { useMutation } from "@opsgrids/client";

/**
 * One production-order action as a mutation that carries an `Idempotency-Key`.
 *
 * The key is chosen from the request body, so retrying the same failed submission (a dropped
 * response, a 5xx) reuses it and the server replays instead of acting twice, while a corrected
 * body gets a fresh key. `replayed` in the response is deliberately not surfaced: a replay looks
 * exactly like the first success. The dialogs that call this are mounted only while open, which is
 * what discards the keys.
 */
export function useOrderAction<Input>(
  call: (input: Input, options: MutationOptions) => Promise<ProductionActionOut>,
  onDone: (result: ProductionActionOut) => void,
) {
  const keyFor = useIdempotencyKeys();
  return useMutation((input: Input) => call(input, { idempotencyKey: keyFor(input) }), {
    onSuccess: (result) => onDone(result),
  });
}
