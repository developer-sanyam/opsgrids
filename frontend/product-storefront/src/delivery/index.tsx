/**
 * The delivery module's entry point.
 *
 * A default export, because the route lazy-loads it: the zone editor, the CSV reader, the plan
 * preview and the demand table are a screen most sessions never open, and a merchant who only
 * edits their theme should not download a postal-code parser. The editor is the app's other split
 * point, for the same reason.
 *
 * The Suspense fallback is `./DeliveryFallback`, imported eagerly by the router — see the note on
 * that file for why it is not here.
 */

import { DeliveryPage } from "./DeliveryPage";

export { DeliveryPage };

export default DeliveryPage;
