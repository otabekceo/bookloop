// Discover "people" filter semantics, kept free of React/Expo imports so they are unit-tested
// (scripts/logic.test.mjs). The screen owns the state; this module owns what it means.

/** Radii (km) offered in the Discover filter sheet. */
export const DISTANCES = [1, 5, 10, 25, 50] as const;

/** "All" distances: no maximum-distance constraint. A distinct value, never a fake large radius. */
export const DISTANCE_ALL = "all" as const;
export type DistanceFilter = number | typeof DISTANCE_ALL;

/** The radius Discover opens with (and returns to on a Discover tab re-tap). "All" is never the default. */
export const DEFAULT_DISTANCE: DistanceFilter = 25;

/** Chips shown in the filter sheet, in order: the numeric radii, then "All". */
export const DISTANCE_OPTIONS: readonly DistanceFilter[] = [...DISTANCES, DISTANCE_ALL];

/**
 * A distance value from anywhere outside the screen's own state (a saved or older value, a route
 * param) made safe: an offered radius or "all" passes through (numeric strings are accepted for
 * values that went through a URL), anything else falls back to the default.
 */
export function normalizeDistance(v: unknown): DistanceFilter {
  if (v === DISTANCE_ALL) return DISTANCE_ALL;
  const n = typeof v === "string" && v.trim() !== "" ? Number(v) : v;
  return typeof n === "number" && (DISTANCES as readonly number[]).includes(n) ? n : DEFAULT_DISTANCE;
}

/** Map area Discover was opened for ("Explore people" on a map cluster), or null for normal Discover. */
export type AreaScope = { cluster: string; area: string };

/** Route params -> area scope. Both the cluster id and its display name are required. */
export function areaScopeFromParams(params: { cluster?: string | string[]; area?: string | string[] }): AreaScope | null {
  const one = (v?: string | string[]) => (Array.isArray(v) ? v[0] : v)?.trim() || "";
  const cluster = one(params.cluster);
  const area = one(params.area);
  return cluster && area ? { cluster, area } : null;
}

export type PeopleFilters = {
  search: string;
  genre: string;
  language: string;
  maxDistance: DistanceFilter;
  exchangingOnly: boolean;
  scope: AreaScope | null;
};

/**
 * Query string for GET /api/discover/people. An area scope replaces the distance filter: the reader
 * picked that area on the map, so its readers are listed however far away it is. Otherwise "All"
 * is sent explicitly as `max_distance=all` and a radius as its number of km.
 */
export function peopleQueryString(f: PeopleFilters): string {
  const p = new URLSearchParams({
    search: f.search,
    genre: f.genre,
    language: f.language,
    exchanging: String(f.exchangingOnly),
  });
  if (f.scope) p.set("cluster", f.scope.cluster);
  else p.set("max_distance", String(f.maxDistance));
  return p.toString();
}
