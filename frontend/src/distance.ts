type Translate = (key: string, options?: Record<string, unknown>) => string;

/** The backend's "distance unknown" value (either reader has no real location yet). */
export const UNKNOWN_DISTANCE_KM = 999;
/** The backend never reports less than this; closer readers are shown as "Within 100 m". */
export const MIN_DISTANCE_KM = 0.1;

export function isKnownDistance(km: number | null | undefined): km is number {
  return typeof km === "number" && km < UNKNOWN_DISTANCE_KM;
}

/**
 * Human distance for another reader, or null when unknown. Anything up to 100 m is shown only as
 * "Within 100 m" — never as 12 m, 37 m… — so the app doesn't reveal how close someone really is.
 * `away` picks "3.2 km away" (true) or the compact "3.2 km" (false).
 */
export function distanceLabel(t: Translate, km: number | null | undefined, away = true): string | null {
  if (!isKnownDistance(km)) return null;
  if (km <= MIN_DISTANCE_KM) return t("location.within100m");
  return away ? t("common.kmAway", { distance: km }) : `${km} km`;
}

/** Area shown for a reader: neighborhood, else city, else "Location not set". */
export function areaLabel(t: Translate, place: { neighborhood?: string | null; city?: string | null }): string {
  return place.neighborhood || place.city || t("location.notSet");
}

/** Distance when known, otherwise the reader's area. */
export function distanceOrArea(
  t: Translate,
  place: { distance_km?: number | null; neighborhood?: string | null; city?: string | null },
  away = true,
): string {
  return distanceLabel(t, place.distance_km, away) ?? areaLabel(t, place);
}
