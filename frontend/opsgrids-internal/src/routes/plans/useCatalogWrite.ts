import { useRef, useState } from "react";

import { problemErrors, problemReference } from "@/api/catalog";
import type { ProblemFieldError } from "@/api/catalog";
import { ApiError } from "@/api/http";

export interface CatalogWrite {
  /** One `Idempotency-Key` for this dialog open, sent on every retry from it. */
  idempotencyKey: string;
  pending: boolean;
  error: string | undefined;
  reference: string | undefined;
  fieldErrors: ProblemFieldError[];
  /** Run the write once at a time; resolves to its result, or `undefined` when it failed (or a
   *  write was already in flight). */
  run: <T>(write: (key: string) => Promise<T>) => Promise<T | undefined>;
}

/**
 * The write half of every catalogue dialog: a key minted once per dialog open (the dialog
 * component mounts fresh each time it opens, which is what earns a new key), a synchronous latch
 * so a double click sends once, and the server's refusal kept verbatim — its sentence, its
 * `errors[]` and its `request_id` — rather than a generic "something went wrong".
 */
export function useCatalogWrite(): CatalogWrite {
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const [reference, setReference] = useState<string | undefined>(undefined);
  const [fieldErrors, setFieldErrors] = useState<ProblemFieldError[]>([]);
  const inFlight = useRef(false);

  async function run<T>(write: (key: string) => Promise<T>): Promise<T | undefined> {
    if (inFlight.current) return undefined;
    inFlight.current = true;
    setPending(true);
    setError(undefined);
    setReference(undefined);
    setFieldErrors([]);
    try {
      return await write(idempotencyKey);
    } catch (err) {
      setFieldErrors(problemErrors(err));
      setReference(problemReference(err));
      setError(
        err instanceof ApiError && err.status >= 500
          ? `${err.message} The outcome is unknown — reload the plan before trying again.`
          : err instanceof Error
            ? err.message
            : "The change did not go through.",
      );
      return undefined;
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  return { idempotencyKey, pending, error, reference, fieldErrors, run };
}
