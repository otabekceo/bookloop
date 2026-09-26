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
  try {
    // allowRTL(false) also stops a right-to-left DEVICE language from forcing RTL onto an LTR app language.
    I18nManager.allowRTL(rtl);
    I18nManager.forceRTL(rtl);
  } catch (e) {
    // Seen to throw on some physical-device/Expo Go native-module setups. Non-fatal: the rest of direction
    // sync (and app boot) must continue even if the native preference couldn't be written this run.
    console.error("[direction] applyNativeDirection threw:", e);
  }
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
 * Upper bound on how long app BOOT waits for a reload it just triggered to actually land (tear down and
 * replace the current JS context) before giving up and rendering in this context anyway. A real reload lands
 * well under this on both platforms; this exists only as a fail-safe for a reload signal that is dropped
 * (observed on some physical-device/Expo Go setups) so startup can never hang forever on it. Deliberately
 * short: this is a silent fallback, not a loading experience to budget for.
 */
const BOOT_RELOAD_FALLBACK_MS = 1500;

export function waitForBootReloadFallback(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, BOOT_RELOAD_FALLBACK_MS));
}

/**
 * Make the native layout direction match the language.
 *  - "in-sync":   nothing to do (same direction, e.g. English -> Italian).
 *  - "reloading": direction changed and a reload was triggered; the caller should stop.
 *  - "pending":   direction differs but a reload is not possible, not allowed, or was already tried; the
 *                 visible layout is still kept correct by the root `direction` style, and the native setting
 *                 applies on the next app start.
 *
 * @param allowReload  Whether this call may trigger `DevSettings.reload()` at all. Defaults to `true` for the
 *                      in-app language switch, which is what actually needs the reload to take effect and
 *                      runs once the app (and its connection to the Metro dev server) is already up. App BOOT
 *                      passes `false`: on some physical-device/Expo Go setups, reloading this early — right
 *                      after cold launch, before the dev-server connection is fully warmed up — makes Expo Go
 *                      try to re-download the JS bundle and can fail with a FATAL native error
 *                      ("Failed to download remote update"), outside anything JS can catch or recover from.
 *                      Skipping the boot-time reload avoids that entirely; the native preference is still
 *                      written above so the direction is correct on the next real (non-reload) app start, and
 *                      the visible layout is already correct via the root `direction` style in the meantime.
 */
export async function syncNativeDirection(
  rtl: boolean,
  /** Awaited right before the reload, and only when a reload will actually happen (used to show the overlay). */
  beforeReload?: () => Promise<void>,
  allowReload = true,
): Promise<DirectionResult> {
  if (Platform.OS === "web") return "in-sync"; // the browser handles direction via <html dir>
  applyNativeDirection(rtl);

  const stored = await storage.getItem<string | null>(MARKER_KEY, null);
  const marker: Dir | null = stored === "rtl" || stored === "ltr" ? stored : null;
  const plan = planDirection(rtl, I18nManager.isRTL, marker);
  console.log(
    `[direction] wantRTL=${rtl} nativeRTL=${I18nManager.isRTL} marker=${marker} allowReload=${allowReload} -> action=${plan.action} nextMarker=${plan.marker}`,
  );

  if (plan.marker) await storage.setItem(MARKER_KEY, plan.marker);
  else if (marker) await storage.removeItem(MARKER_KEY);

  if (plan.action === "reload") {
    if (!allowReload) {
      console.log("[direction] reload wanted but disallowed by caller (boot) -> pending");
      return "pending";
    }
    if (!canReload()) {
      console.log("[direction] reload wanted but canReload() is false -> pending"); // e.g. release build
      return "pending";
    }
    if (beforeReload) await beforeReload();
    const started = reloadApp();
    console.log(`[direction] reloadApp() returned ${started}`);
    return started ? "reloading" : "pending";
  }
  return plan.action === "none" ? "in-sync" : "pending";
}
