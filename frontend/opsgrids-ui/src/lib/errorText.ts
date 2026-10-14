/**
 * "Request failed with 500" is what an HTTP client says to a developer. This is what to say to a
 * person: what happened, in words, and (for the ones that can fix themselves) what to do.
 */
export function plainErrorMessage(message: string): string {
  const match = /(?:failed with|status(?: code)?)\s*(?:status code\s*)?(\d{3})/i.exec(message) ?? /^(\d{3})\b/.exec(message);
  const status = match?.[1] === undefined ? undefined : Number(match[1]);
  if (status === undefined) return message;
  if (status === 401) return "Your session has ended. Sign in again to continue.";
  if (status === 403) return "You don't have access to this. Ask an administrator if you need it.";
  if (status === 404) return "We couldn't find that. It may have been moved or deleted.";
  if (status === 408 || status === 504) return "The request took too long. Check your connection and try again.";
  if (status === 429) return "Too many requests in a short time. Wait a moment, then try again.";
  if (status >= 500) return "Something went wrong on our side. Try again in a moment.";
  return message;
}
