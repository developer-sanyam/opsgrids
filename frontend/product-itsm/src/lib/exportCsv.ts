/**
 * A bulk selection as a CSV the browser downloads — the shared implementation in
 * `@opsgrids/client`. Nothing is sent anywhere, so this needs no permission beyond the read the
 * list itself already required.
 */
export { downloadCsvFile as downloadCsv, toCsvGrid as toCsv } from "@opsgrids/client";
