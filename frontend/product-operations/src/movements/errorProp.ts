/**
 * `{ error }` when there is a message, `{}` when there is not — for spreading onto a form control
 * under `exactOptionalPropertyTypes`, where `error: undefined` is not the same as no `error`. The
 * message is the client's own check if there is one, else the server's sentence for that field.
 */
export function errorProp(...messages: (string | undefined)[]): { error?: string } {
  const message = messages.find((m) => m !== undefined && m !== "");
  return message === undefined ? {} : { error: message };
}
