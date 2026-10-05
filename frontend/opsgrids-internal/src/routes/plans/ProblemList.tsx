import type { ProblemFieldError } from "@/api/catalog";

/**
 * Every refusal the server listed in `errors[]` — publish validation names each problem with the
 * sheet so all of them can be fixed in one pass. Rendered under the form-level error, which
 * already carries the server's summary sentence.
 */
export function ProblemList({ problems }: { problems: ProblemFieldError[] }) {
  if (problems.length === 0) return null;
  return (
    <ul aria-label="What needs fixing" className="flex list-disc flex-col gap-1 pl-6 text-sm text-ink">
      {problems.map((problem) => (
        <li key={`${problem.field}-${problem.code}`}>
          {problem.message}
          {problem.field ? <span className="mono ml-2 text-xs text-muted">{problem.field}</span> : null}
        </li>
      ))}
    </ul>
  );
}
