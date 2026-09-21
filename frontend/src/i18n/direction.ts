import { DevSettings, I18nManager, Platform } from "react-native";

import { storage } from "@/src/utils/storage";
import { planDirection, type Dir } from "./directionPlan";
import type { DirectionResult } from "./languageSwitch";

/**
 * Native layout direction, kept in step with the selected language.
 *
 * React Native decides the layout direction once, when the app starts (`I18nManager.isRTL`), from a
 * preference stored on the device. It cannot change while the app runs: switching between a left-to-right
 * and a right-to-left language needs a reload to take effect. Two rules keep this reliable:
 *  1. The wanted direction is ALWAYS written to the native preference. It used to be written only when it
 *     differed from `isRTL`, but `isRTL` is frozen at startup, so Arabic -> English in the same session
 *     never cleared the stored RTL preference and the next launch came back mirrored.
 *  2. The app reloads only when the direction the native layer is using differs from the wanted one
 *     (LTR <-> RTL). Switching between two LTR languages never reloads.
 */

const MARKER_KEY = "bookloop_direction_reload";

// Keep `left`/`right` style props meaning the physical sides in every direction. By default React Native
// swaps them in RTL, which would double-flip the styles the app already mirrors itself (see
// `useDirectionalStyle` in ui.tsx). Logical `start`/`end` and `flexDirection: "row"` still follow the direction.
if (Platform.OS !== "web") {
  try {
    I18nManager.swapLeftAndRightInRTL(false);
  } catch {
    // older runtimes: harmless
  }
}

/** Store the wanted direction natively (applies at the next start/reload). Idempotent; always safe to call. */
function applyNativeDirection(rtl: boolean) {
  // allowRTL(false) also stops a right-to-left DEVICE language from forcing RTL onto an LTR app language.
  I18nManager.allowRTL(rtl);
  I18nManager.forceRTL(rtl);
}

// Reloads the JS bundle in development / Expo Go. Release builds need `expo-updates`
// (`Updates.reloadAsync()`); until that is added they fall back to applying the direction on next start.
function canReload(): boolean {
  return !!__DEV__ && !!DevSettings && typeof DevSettings.reload === "function";
}

function reloadApp(): boolean {
  try {
    if (canReload()) {
      DevSettings.reload();
      return true;
    }
  } catch {
    // fall through
  }
  return false;
}

/** Time the "switching language" overlay is given to appear before the reload (kept short on purpose). */
const OVERLAY_PAINT_MS = 200;

/** One frame for React to commit the overlay, then a moment for the native Modal to present and be seen. */
export function waitForOverlayPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, OVERLAY_PAINT_MS)));
}

/**
 * Make the native layout direction match the language.
 *  - "in-sync":   nothing to do (same direction, e.g. English -> Italian).
 *  - "reloading": direction changed and a reload was triggered; the caller should stop.
 *  - "pending":   direction differs but a reload is not possible or was already tried; the visible layout
 *                 is still kept correct by the root `direction` style, and the native setting applies on
 *                 the next app start.
 */
export async function syncNativeDirection(
  rtl: boolean,
  /** Awaited right before the reload, and only when a reload will actually happen (used to show the overlay). */
  beforeReload?: () => Promise<void>,
): Promise<DirectionResult> {
  if (Platform.OS === "web") return "in-sync"; // the browser handles direction via <html dir>
  applyNativeDirection(rtl);

  const stored = await storage.getItem<string | null>(MARKER_KEY, null);
  const marker: Dir | null = stored === "rtl" || stored === "ltr" ? stored : null;
  const plan = planDirection(rtl, I18nManager.isRTL, marker);

  if (plan.marker) await storage.setItem(MARKER_KEY, plan.marker);
  else if (marker) await storage.removeItem(MARKER_KEY);

  if (plan.action === "reload") {
    if (!canReload()) return "pending"; // nothing to show an overlay for: no reload will happen
    if (beforeReload) await beforeReload();
    return reloadApp() ? "reloading" : "pending";
  }
  return plan.action === "none" ? "in-sync" : "pending";
}
