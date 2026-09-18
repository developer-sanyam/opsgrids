/** A store page's route, as a URL inside this app. `"/"` is the store home. */
export function storeHref(route: string): string {
  return route === "/" ? "/store" : `/store${route}`;
}
