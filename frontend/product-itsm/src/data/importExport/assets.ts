import { RowProblem, collectPages, defineImportEntity, minorToMajor } from "@opsgrids/client";
import type { ImportColumn, SheetValue, UpdateSpec } from "@opsgrids/client";

import { createAsset, listAssets, updateAsset } from "@/api/client";
import type { Asset, AssetIn, AssetStatus, AssetType, AssetUpdate } from "@/api/types";
import { assetStatusLabel, assetTypeLabel } from "@/lib/format";

import {
  labelOfOption,
  numberOf,
  optionOf,
  requiredTextOf,
  sameKey,
  storedText,
  textOf,
  upperCased,
} from "./rowValues";

/** What the Assets screen tracks, and so what an export of it must respect. */
export interface AssetFilters {
  search: string;
  type: AssetType | "all";
  status: AssetStatus | "all";
}

const ASSET_TYPES: readonly AssetType[] = [
  "laptop",
  "desktop",
  "server",
  "monitor",
  "phone",
  "network",
  "license",
];
const TYPE_OPTIONS = ASSET_TYPES.map((value) => ({ value, label: assetTypeLabel(value) }));

const ASSET_STATUSES: readonly AssetStatus[] = ["in_use", "in_stock", "in_repair", "retired"];
const STATUS_OPTIONS = ASSET_STATUSES.map((value) => ({ value, label: assetStatusLabel(value) }));

/** The currency an asset's cost is in when the row names none, on the sheet and on the wire. */
const DEFAULT_CURRENCY = "INR";

const columns: readonly ImportColumn[] = [
  {
    key: "tag",
    header: "Asset tag",
    kind: "text",
    required: true,
    maxLength: 40,
    pattern: /^[A-Za-z0-9_-]+$/,
    patternHint: "Letters, digits, hyphens and underscores, with no spaces.",
    help: "The tag on the asset. It must be unique and cannot be changed later.",
  },
  {
    key: "name",
    header: "Name",
    kind: "text",
    required: true,
    maxLength: 200,
    help: "What the asset is, for example Dell Latitude 7440.",
  },
  {
    key: "type",
    header: "Type",
    kind: "enum",
    required: true,
    options: TYPE_OPTIONS,
    help: "The kind of asset.",
  },
  {
    key: "status",
    header: "Status",
    kind: "enum",
    options: STATUS_OPTIONS,
    help: "Where the asset is in its life. Leave blank for In stock.",
  },
  {
    key: "owner",
    header: "Owner",
    kind: "text",
    maxLength: 120,
    help: "Who has it: a person's name or a team. Free text.",
  },
  {
    key: "location",
    header: "Location",
    kind: "text",
    maxLength: 160,
    help: "Where it is kept, for example Bengaluru HQ, 3rd floor.",
  },
  {
    key: "serial",
    header: "Serial number",
    kind: "text",
    maxLength: 120,
    help: "The manufacturer's serial number.",
  },
  {
    key: "purchased_at",
    header: "Purchase date",
    kind: "date",
    help: "The day it was bought, for example 2026-04-01.",
  },
  {
    key: "warranty_expires_at",
    header: "Warranty expires",
    kind: "date",
    help: "The day the warranty or support ends, for example 2029-03-31.",
  },
  {
    key: "cost",
    header: "Cost",
    kind: "money",
    min: 0,
    currencyKey: "cost_currency",
    defaultCurrency: DEFAULT_CURRENCY,
    help: "What it cost, in whole currency units and not in paise or cents, for example 84500 or 84500.50.",
  },
  {
    key: "cost_currency",
    header: "Cost currency",
    kind: "text",
    maxLength: 3,
    pattern: /^[A-Za-z]{3}$/,
    patternHint: "A three-letter currency code such as INR or USD.",
    defaultCurrency: DEFAULT_CURRENCY,
    help: `The currency of the cost. Leave blank for ${DEFAULT_CURRENCY}.`,
  },
  {
    key: "open_incidents",
    header: "Open incidents",
    kind: "integer",
    readOnly: true,
    help: "How many incidents are open against this asset. Counted by the system.",
  },
];

const sampleRows: readonly Record<string, SheetValue>[] = [
  {
    tag: "LT-2041",
    name: "Dell Latitude 7440",
    type: "Laptop",
    status: "In use",
    owner: "Anita Desai",
    location: "Bengaluru HQ, 3rd floor",
    serial: "8H2K3Z3",
    purchased_at: "2025-08-14",
    warranty_expires_at: "2028-08-13",
    cost: "84500.00",
    cost_currency: "INR",
  },
  {
    tag: "SW-0310",
    name: "Microsoft 365 Business Premium",
    type: "License",
    status: "In use",
    owner: "IT Operations",
    purchased_at: "2026-04-01",
    warranty_expires_at: "2027-03-31",
    cost: "1200.00",
    cost_currency: "USD",
  },
  {
    tag: "MN-0087",
    name: "Dell U2723QE 27-inch monitor",
    type: "Monitor",
    status: "In stock",
    location: "Pune office store room",
    serial: "CN0X4F1",
    purchased_at: "2026-02-20",
    warranty_expires_at: "2029-02-19",
    cost: "32000.00",
  },
];

const notes = [
  "Asset tags must be unique. With \"Add new records only\", a row whose tag already exists is skipped, never updated.",
  "A tag that once belonged to an asset that was later deleted still cannot be used again. The server reports it as a conflict without saying why, so a row that is refused as already existing may be a deleted asset's tag: choose another tag.",
  "Every imported asset is recorded as belonging to the person who runs the import, whatever the Owner column says: Owner is only the name shown. A person who can see only their own assets sees exactly the ones they imported.",
  `Cost is written in whole currency units, for example 84500 or 84500.50, with the currency in the next column. A blank currency means ${DEFAULT_CURRENCY}.`,
  "Owner is free text, such as a person's name or a team. It is not matched against your users.",
  "Dates can be Excel dates or written as YYYY-MM-DD, for example 2026-04-01.",
  "Updating: the asset tag says which asset a row is about and cannot be changed. An export has no Record ID for assets, so the tag is the only way to name one.",
  "Updating: [clear] empties Owner, Location, Serial number, Purchase date and Warranty expires. Name, Type, Status, Cost and Cost currency cannot be emptied: leave them blank to keep them.",
  "Updating: a blank Cost currency keeps the asset's own currency, so a cost is read in that currency and not in INR. Changing the currency does not convert the cost: a row that changes it must also give Cost in the new currency, or it is refused.",
  "Updating: changing Owner changes only the name shown, not whose asset it is for permissions. A person who can see only their own assets cannot see, and so cannot update, anyone else's.",
];

/**
 * How an existing asset is updated from a sheet. `PATCH /itsm/assets/{tag}` takes the tag in the
 * URL and refuses it in the body, refuses an empty body, forbids unknown keys and has no null
 * guard (a null on a required column is a 409, and on `cost_currency` a 500). So the tag is
 * `immutable`, only the three columns the survey found nullable are cleared with `null`, Location
 * and Serial with `""` (their columns are never null), and cost and its currency are never
 * cleared. A currency change also needs the cost: the server relabels an amount and does not
 * convert it.
 */
const assetUpdate: UpdateSpec<Asset> = {
  loadExisting: (ctx) =>
    collectPages((page, size) => listAssets({ page, size }), {
      signal: ctx.signal,
      key: (asset) => asset.tag,
    }),
  // An asset has no id column: its tag is both the natural key and what the URL carries.
  idOf: (asset) => asset.tag,
  keyColumn: "tag",
  keyOf: (asset) => asset.tag,
  currencyOf: (asset) => asset.cost_currency,
  fields: [
    { column: "tag", current: (a) => a.tag, immutable: true, same: sameKey },
    { column: "name", current: (a) => a.name },
    { column: "type", current: (a) => a.type },
    { column: "status", current: (a) => a.status },
    { column: "owner", current: (a) => storedText(a.owner), clear: null },
    { column: "location", current: (a) => storedText(a.location), clear: "" },
    { column: "serial", current: (a) => storedText(a.serial), clear: "" },
    { column: "purchased_at", current: (a) => a.purchased_at, clear: null },
    { column: "warranty_expires_at", current: (a) => a.warranty_expires_at, clear: null },
    { column: "cost", field: "cost_minor" satisfies keyof AssetUpdate, current: (a) => a.cost_minor },
    { column: "cost_currency", current: (a) => a.cost_currency.toUpperCase(), resolve: upperCased },
  ],
  check(row, asset) {
    const named = textOf(row, "cost_currency")?.toUpperCase();
    if (named === undefined || named === asset.cost_currency.toUpperCase()) return;
    if (numberOf(row, "cost") === null) {
      throw new RowProblem(
        `Cost currency would change from ${asset.cost_currency} to ${named}, but changing it does not convert the cost. Give Cost in ${named} too, or leave Cost currency blank.`,
      );
    }
  },
  async send(asset, body) {
    // The tag is the URL's, never the body's. The body holds only the keys this spec's fields
    // name, all of them keys of `AssetUpdate`.
    await updateAsset(asset.tag, body as AssetUpdate);
  },
};

export const assetsEntity = defineImportEntity<AssetIn, Asset, AssetFilters>({
  id: "itsm.assets",
  noun: "assets",
  nounSingular: "asset",
  fileStem: "assets",
  columns,
  sampleRows,
  notes,
  update: assetUpdate,
  toPayload(row) {
    const type = optionOf(row, "type", TYPE_OPTIONS);
    if (type === null) throw new RowProblem("Type is required.");
    const body: AssetIn = {
      tag: requiredTextOf(row, "tag", "Asset tag"),
      name: requiredTextOf(row, "name", "Name"),
      type,
    };
    const status = optionOf(row, "status", STATUS_OPTIONS);
    if (status !== null) body.status = status;
    const owner = textOf(row, "owner");
    if (owner !== null) body.owner = owner;
    const location = textOf(row, "location");
    if (location !== null) body.location = location;
    const serial = textOf(row, "serial");
    if (serial !== null) body.serial = serial;
    const purchased = textOf(row, "purchased_at");
    if (purchased !== null) body.purchased_at = purchased;
    const warranty = textOf(row, "warranty_expires_at");
    if (warranty !== null) body.warranty_expires_at = warranty;
    // The engine fills a blank currency cell with the default, so the currency column says nothing
    // about whether one was written: it is sent with a cost, and only then.
    const cost = numberOf(row, "cost");
    if (cost !== null) {
      body.cost_minor = cost;
      body.cost_currency = (textOf(row, "cost_currency") ?? DEFAULT_CURRENCY).toUpperCase();
    }
    return body;
  },
  async create(payload) {
    const asset = await createAsset(payload);
    return { id: asset.tag };
  },
  export: {
    fetchPage: ({ page, size, filters }) =>
      listAssets({
        search: filters.search,
        type: filters.type,
        status: filters.status,
        page,
        size,
      }),
    toRow: (asset) => ({
      tag: asset.tag,
      name: asset.name,
      type: labelOfOption(TYPE_OPTIONS, asset.type),
      status: labelOfOption(STATUS_OPTIONS, asset.status),
      owner: asset.owner,
      location: asset.location,
      serial: asset.serial,
      purchased_at: asset.purchased_at,
      warranty_expires_at: asset.warranty_expires_at,
      cost: minorToMajor(asset.cost_minor, asset.cost_currency),
      cost_currency: asset.cost_currency,
      open_incidents: asset.open_incident_ids.length,
    }),
  },
});
