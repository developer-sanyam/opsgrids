/**
 * The shortest password the server accepts (`Password` in auth-service's schemas: min_length 8).
 * Every screen that sets or changes a password states and checks this one number, so the helper
 * text on one never contradicts the refusal on another. The server stays the judge.
 */
export const MIN_PASSWORD_LENGTH = 8;
