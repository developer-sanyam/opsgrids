/**
 * This app's UI barrel: the shared design system, re-exported, plus the two things that are only
 * this app's — its mark and its navigation. Anything general belongs in `@opsgrids/ui` (ADR-0001).
 */

export { Button, Card, PageHeader } from "@opsgrids/ui";

export { LogoMark } from "./LogoMark";
export { useNavGroups } from "./useNavGroups";
export { AuthCard, Field, InlineError, inputClass } from "./forms";
