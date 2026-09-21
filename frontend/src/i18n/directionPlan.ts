// Pure decision logic for keeping the NATIVE layout direction in step with the language.
// No imports on purpose, so it can be run and tested outside React Native.

export type Dir = "rtl" | "ltr";

export type DirectionPlan = {
  /** none: already in sync · reload: switch direction by reloading · pending: needs a restart we can't force */
  action: "none" | "reload" | "pending";
  /** Value to store as the loop guard (null = clear it). */
  marker: Dir | null;
};

/**
 * @param wantRTL   direction of the selected language
 * @param nativeRTL direction the native layout engine is using in THIS run. React Native fixes it at
 *                  startup (I18nManager.isRTL) and it only changes after the JS bundle is reloaded.
 * @param marker    direction we last tried to reload into; stops an endless reload loop when the reload
 *                  did not actually change the native direction.
 */
export function planDirection(wantRTL: boolean, nativeRTL: boolean, marker: Dir | null): DirectionPlan {
  if (wantRTL === nativeRTL) return { action: "none", marker: null }; // in sync (also clears the guard)
  const target: Dir = wantRTL ? "rtl" : "ltr";
  if (marker === target) return { action: "pending", marker }; // already reloaded for this once: don't loop
  return { action: "reload", marker: target };
}
