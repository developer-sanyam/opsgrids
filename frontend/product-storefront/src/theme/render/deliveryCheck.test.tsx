/**
 * The buyer's serviceability check, as it renders.
 *
 * The suite is organised around the one rule the whole feature hangs off (ADR-0010 §13):
 *
 * > A check that fails renders as "we couldn't check", never as "we don't deliver here". Unknown
 * > is not no.
 *
 * So the three outcomes are asserted to read *distinctly*, not merely to render — a suite that
 * only checked "something appeared" would pass against the exact defect this section exists to
 * prevent, which is a transient failure painted as a refusal.
 *
 * Everything is driven through `settings`, with no provider, no router and no backend, because
 * that is what the editor's canvas and the gallery's miniature hand it.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ServiceabilityOut } from "@/api/publicClient";
import { postalCodeStorageKey } from "@/lib/serviceability";
import { defaultSettings } from "../sections";
import type { SettingValue, StoreMode } from "../schema";
import { DeliveryCheck } from "./DeliveryCheck";

vi.mock("@/api/publicClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/publicClient")>();
  return { ...actual, checkServiceability: vi.fn() };
});

const { checkServiceability } = await import("@/api/publicClient");
const asked = vi.mocked(checkServiceability);

const PHONE_WIDTH = 390;
const DESKTOP_WIDTH = 1200;

const SLUG = "quickcart";

/** A served answer in the shape `libs/contracts/openapi/storefront.json` publishes. */
function answer(overrides: Partial<ServiceabilityOut> = {}): ServiceabilityOut {
  return {
    postal_code: "400050",
    serviceable: true,
    mode: "PROMISED",
    reason: null,
    promise: { kind: "DURATION", minutes: 25 },
    fees: {
      currency: "INR",
      delivery_fee_minor: 2900,
      free_delivery_above_minor: 49900,
      min_order_minor: 9900,
      display_only: true,
    },
    buyer_note: null,
    checked_at: "2026-09-19T09:15:00Z",
    ...overrides,
  };
}

/** Never mapped. `mode` is `UNSERVED` — the same `mode` the other two refusals carry. */
const UNSERVED = answer({
  postal_code: "422001",
  serviceable: false,
  mode: "UNSERVED",
  reason: "no_mapping",
  promise: null,
  fees: null,
});

/** Mapped and deliberately excluded. Same `mode`, different sentence. */
const EXCLUDED = answer({
  postal_code: "194101",
  serviceable: false,
  mode: "UNSERVED",
  reason: "excluded",
  promise: null,
  fees: null,
});

/** No `MemoryRouter` and no `LineListProvider`: the section must not need either. */
function renderCheck(
  overrides: Record<string, SettingValue> = {},
  { width = DESKTOP_WIDTH, mode = "retail" as StoreMode } = {},
) {
  return render(
    <DeliveryCheck
      settings={{ ...defaultSettings("delivery-check"), storeSlug: SLUG, ...overrides }}
      products={[]}
      categories={[]}
      stacked={width < 768}
      width={width}
      mode={mode}
    />,
  );
}

/** Type into a check that is already on screen. */
async function submit(code: string) {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "PIN code" }), code);
  await user.click(screen.getByRole("button", { name: "Check" }));
  return user;
}

/** Render a retail check and ask it about a postal code — the common path through every test. */
async function check(code: string, options: { width?: number } = {}) {
  renderCheck({}, options);
  return submit(code);
}

beforeEach(() => {
  window.localStorage.clear();
  asked.mockResolvedValue(answer());
});

/* ------------------------------ the three outcomes ------------------------- */

describe("serviceable", () => {
  it("shows the promise the server computed, not one it worked out itself", async () => {
    await check("400050");

    expect(await screen.findByText("Delivery in 25 minutes")).not.toBeNull();
    expect(screen.getByText(/We deliver to 400050/)).not.toBeNull();
  });

  it("renders a promised date exactly as the server resolved it", async () => {
    // The date is computed in the *store's* timezone. Nothing here re-reads it in the browser's,
    // and nothing adds an offset — "Wed 23 Sep" is the 23rd wherever the buyer is sitting.
    asked.mockResolvedValue(
      answer({
        promise: { kind: "DATE_OFFSET", date: "2026-09-23" },
        fees: { currency: "INR", delivery_fee_minor: 0, display_only: true },
      }),
    );

    await check("421302");

    expect(await screen.findByText("Arrives Wed 23 Sep")).not.toBeNull();
    // No time of day, no "in 4 days", no year the server did not send.
    expect(screen.queryByText(/2026-09-23/)).toBeNull();
  });

  it("says the fee and the thresholds the zone carries, in whole rupees", async () => {
    await check("400050");

    expect(await screen.findByText(/₹29 delivery · free over ₹499 · ₹99 minimum/)).not.toBeNull();
  });

  it("says free delivery in words rather than as an amount of nothing", async () => {
    asked.mockResolvedValue(
      answer({
        fees: {
          currency: "INR",
          delivery_fee_minor: 0,
          free_delivery_above_minor: 49900,
          display_only: true,
        },
      }),
    );

    await check("400050");

    expect(await screen.findByText(/Free delivery/)).not.toBeNull();
    expect(screen.queryByText(/₹0/)).toBeNull();
  });

  it("takes the currency from the fees, which is the only place it is sent", async () => {
    /*
     * There is no top-level `currency` on the answer and there never was. A build that read one
     * found `undefined`, defaulted to rupees, and would have printed a Dubai store's dirhams as ₹.
     */
    asked.mockResolvedValue(
      answer({
        fees: { currency: "AED", delivery_fee_minor: 1500, min_order_minor: 0, display_only: true },
      }),
    );

    await check("400050");

    expect(await screen.findByText(/AED\s?15 delivery/)).not.toBeNull();
    expect(screen.queryByText(/₹15/)).toBeNull();
  });

  it("says where the window is picked for a slotted zone, and promises no time", async () => {
    // `mode` answers the second question — what kind of served this is. It is the only field that
    // can, and it is asked only after `serviceable` has answered the first.
    asked.mockResolvedValue(answer({ mode: "SLOTTED", promise: null }));

    await check("400050");

    expect(await screen.findByText(/We deliver to 400050/)).not.toBeNull();
    expect(screen.getByText(/Delivery windows are shown at checkout/)).not.toBeNull();
    expect(screen.queryByText(/Delivery in/)).toBeNull();
  });
});

describe("not serviceable", () => {
  it("says so plainly, names the code, and offers the one thing that is true", async () => {
    asked.mockResolvedValue(UNSERVED);

    await check("422001");

    expect(await screen.findByText("We don't deliver to 422001 yet")).not.toBeNull();
    // The check itself is the demand signal (§7), so this is an honest promise rather than a form
    // that goes nowhere.
    expect(screen.getByText("We've recorded the request — we'll let you know when we get there.")).not.toBeNull();
  });

  it("does not promise to expand into an area the merchant deliberately excluded", async () => {
    /*
     * Both refusals arrive with `mode: "UNSERVED"`; only `reason` tells them apart, which is why
     * the client classifies on `serviceable` and then branches on `reason`. "We'll let you know
     * when we get there" is a promise nobody intends to keep for an excluded postal code, and
     * "yet" says the same thing in one word.
     */
    asked.mockResolvedValue(EXCLUDED);

    await check("194101");

    expect(await screen.findByText("We don't deliver to 194101")).not.toBeNull();
    expect(screen.getByText("That's outside the area this store covers.")).not.toBeNull();
    expect(screen.queryByText(/we'll let you know when we get there/i)).toBeNull();
    // Still a refusal the server computed, so still not the "couldn't check" state.
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("is not confused with a failure — no retry button, no talk of not being able to check", async () => {
    asked.mockResolvedValue(UNSERVED);

    await check("422001");
    await screen.findByText("We don't deliver to 422001 yet");

    expect(screen.queryByText(/couldn't check/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("couldn't check — the state everybody gets wrong", () => {
  it("renders a failure as unknown, and never as a refusal", async () => {
    asked.mockRejectedValue(new Error("Couldn't reach the delivery check."));

    await check("400050");

    expect(await screen.findByText("We couldn't check right now")).not.toBeNull();
    /*
     * The assertion that matters. A `catch → not serviceable` would put "we don't deliver to
     * 400050" in front of a buyer standing inside a served area, and that buyer does not come
     * back. A Redis outage alone is enough to cause it: the gateway's rate limiter fails closed,
     * so the whole public check 503s.
     */
    expect(screen.queryByText(/don't deliver/)).toBeNull();
    expect(screen.getByText(/connection problem, not a no/)).not.toBeNull();
  });

  it("reads differently from a refusal for a screen reader too, not only by colour", async () => {
    asked.mockRejectedValue(new Error("boom"));

    await check("400050");
    await screen.findByText("We couldn't check right now");

    expect(screen.getByText("This is not an answer about whether we deliver to you.")).not.toBeNull();
  });

  it("offers a retry that asks again, and lands on the real answer", async () => {
    asked.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(answer());
    const user = await check("400050");

    await user.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Delivery in 25 minutes")).not.toBeNull();
    expect(asked).toHaveBeenCalledTimes(2);
  });

  it("renders a 200 it cannot interpret as unknown, not as a refusal", async () => {
    /*
     * The failure that is not a transport failure: a well-formed HTTP 200 whose body this build
     * cannot read — a newer service, a proxy that rewrote it, a verdict that is not a boolean.
     * It resolves rather than throws, so it reaches a different code path from every other test in
     * this block, and it must land in the same place.
     */
    asked.mockResolvedValue(answer({ serviceable: "maybe" as unknown as boolean }));

    await check("400050");

    expect(await screen.findByText("We couldn't check right now")).not.toBeNull();
    expect(screen.queryByText(/don't deliver/)).toBeNull();
  });

  it("treats a 404 store and a 503 alike — a buyer cannot act on the difference", async () => {
    asked.mockRejectedValue(Object.assign(new Error("Not found"), { status: 404 }));

    await check("400050");

    expect(await screen.findByText("We couldn't check right now")).not.toBeNull();
  });
});

/* -------------------------------- the form -------------------------------- */

describe("the postal code field", () => {
  it("keeps a shape the server would refuse off the network, and says why on the field", async () => {
    await check("4007");

    const field = screen.getByRole("textbox", { name: "PIN code" });
    expect(asked).not.toHaveBeenCalled();
    expect(field.getAttribute("aria-invalid")).toBe("true");

    // The message is tied to the input, not floating above the form.
    const describedBy = field.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")?.textContent).toBe(
      "Enter six digits, like 400072.",
    );
  });

  it("keeps what was typed when the check fails", async () => {
    asked.mockRejectedValue(new Error("boom"));

    await check("400050");
    await screen.findByText("We couldn't check right now");

    expect(screen.getByRole("textbox", { name: "PIN code" })).toHaveProperty("value", "400050");
  });

  it("cannot be submitted twice by a double click", async () => {
    let release: (value: ServiceabilityOut) => void = () => {};
    asked.mockReturnValue(new Promise<ServiceabilityOut>((resolve) => (release = resolve)));

    renderCheck();
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: "PIN code" }), "400050");
    const button = screen.getByRole("button", { name: "Check" });
    await user.click(button);

    expect(screen.getByRole("button", { name: "Checking…" }).hasAttribute("disabled")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Checking…" }));
    release(answer());

    await screen.findByText("Delivery in 25 minutes");
    expect(asked).toHaveBeenCalledTimes(1);
  });

  it("names the store in the request, because the gateway's public path cannot carry one", async () => {
    await check("400 050");

    // Normalised on the way out: the merchant's demand report counts one code, not three spellings.
    expect(asked).toHaveBeenCalledWith({ store: SLUG, postal_code: "400050" });
  });
});

/* ------------------------------ what is stored ----------------------------- */

describe("what the browser keeps", () => {
  it("stores the code the buyer typed and never the answer", async () => {
    await check("400050");
    await screen.findByText("Delivery in 25 minutes");

    expect(window.localStorage.getItem(postalCodeStorageKey(SLUG))).toBe("400050");
    expect(Object.keys(window.localStorage)).toEqual([postalCodeStorageKey(SLUG)]);
  });

  it("re-asks on load rather than trusting anything it remembered", async () => {
    window.localStorage.setItem(postalCodeStorageKey(SLUG), "400050");

    renderCheck();

    // The stored code paints the field instantly; the verdict is fetched again every time, because
    // a zone a merchant has since shrunk must stop delivering (ADR-0010 §5).
    expect(screen.getByRole("textbox", { name: "PIN code" })).toHaveProperty("value", "400050");
    await waitFor(() => expect(asked).toHaveBeenCalledWith({ store: SLUG, postal_code: "400050" }));
    expect(await screen.findByText("Delivery in 25 minutes")).not.toBeNull();
  });
});

/* ------------------------------ where it renders --------------------------- */

describe("with no provider and no backend", () => {
  it("settles on the merchant's own promise instead of spinning forever", () => {
    asked.mockRejectedValue(new Error("there is no storefront service"));

    renderCheck({ idlePromiseText: "Delivery in 11 minutes" });

    // The editor canvas and the gallery miniature get exactly this. A section that spun here would
    // make the whole theme unpickable.
    expect(screen.getByText("Delivery in 11 minutes")).not.toBeNull();
    expect(screen.queryByText("Checking…")).toBeNull();
    expect(asked).not.toHaveBeenCalled();
  });

  it("is a labelled region with a real form and a named field", () => {
    renderCheck();

    const region = screen.getByRole("region", { name: "Delivery check" });
    const form = within(region).getByRole("form", { name: "Check delivery to your area" });
    expect(within(form).getByRole("textbox", { name: "PIN code" })).not.toBeNull();
    expect(within(form).getByRole("button", { name: "Check" })).not.toBeNull();
    // Present before there is anything to say, so a reader is watching when it changes.
    expect(within(region).getByRole("status")).not.toBeNull();
  });

  it("is typable and answerable at 390px, where the whole retail library is designed", async () => {
    await check("400050", { width: PHONE_WIDTH });

    expect(await screen.findByText("Delivery in 25 minutes")).not.toBeNull();
  });

  it("asks nothing from inside a gallery miniature", async () => {
    window.localStorage.setItem(postalCodeStorageKey(SLUG), "400050");

    render(
      // What `ThemePreview` wraps a whole store page in: out of the tab order, out of hit-testing.
      <div inert>
        <DeliveryCheck
          settings={{ ...defaultSettings("delivery-check"), storeSlug: SLUG }}
          products={[]}
          categories={[]}
          stacked={false}
          width={DESKTOP_WIDTH}
          mode="retail"
        />
      </div>,
    );

    /*
     * The gallery draws one miniature per theme. Without this guard, opening it would post a check
     * per miniature per page view — and each would land in the merchant's own demand report as a
     * postal code no buyer typed, which is the one signal they have before their first order.
     */
    await waitFor(() => expect(asked).not.toHaveBeenCalled());
  });

  it("asks nothing until the merchant has said which store to ask about", () => {
    window.localStorage.setItem(postalCodeStorageKey(SLUG), "400050");

    renderCheck({ storeSlug: "" });

    expect(asked).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText(/Add the store handle/)).not.toBeNull();
  });
});

describe("the retail guard", () => {
  it("makes no delivery claim on a trade store, and asks nothing", () => {
    window.localStorage.setItem(postalCodeStorageKey(SLUG), "400050");

    renderCheck({ idlePromiseText: "Delivery in 11 minutes" }, { mode: "trade" });

    /*
     * A wholesaler's buyer builds a line list and enquires; there is no doorstep promise to make,
     * and a delivery bar on a trade page claims one by being there. The guard also keeps the
     * section off the network, so a trade store never appears in a delivery demand report.
     */
    expect(asked).not.toHaveBeenCalled();
    expect(screen.queryByText("Delivery in 11 minutes")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText(/for retail stores/)).not.toBeNull();
  });
});
