// The language-switching flow, kept free of React / React Native imports so it can be tested on its own.
// LanguageProvider wires the real implementations in.

export type DirectionResult = "in-sync" | "reloading" | "pending";

export type SwitchDeps = {
  /** Persist the chosen language (AsyncStorage). */
  persist(code: string): Promise<void>;
  /** Make the UI use the language: i18n.changeLanguage + React state. */
  applyLanguage(code: string): Promise<void>;
  /**
   * Store the wanted layout direction natively. When (and only when) a reload will actually happen,
   * `beforeReload` is awaited first, right before the reload is triggered.
   */
  syncDirection(code: string, beforeReload: () => Promise<void>): Promise<DirectionResult>;
  setSwitching(switching: boolean): void;
  /** Resolves once the overlay has had time to appear on screen. */
  waitForPaint(): Promise<void>;
  schedule(fn: () => void, ms: number): void;
};

/**
 * Returns `switchLanguage(code)`.
 *  - LTR <-> LTR (or same direction): saves and applies the language. No overlay, no reload.
 *  - LTR <-> RTL: saves the language FIRST, applies it (so the overlay text is in the NEW language), shows
 *    the overlay, waits just long enough for it to paint, then reloads.
 *  - While a switch is in progress every further call is ignored: no repeated writes, overlay or reloads.
 */
export function createLanguageSwitcher(deps: SwitchDeps, safetyMs = 5000) {
  let busy = false;

  return async function switchLanguage(code: string): Promise<boolean> {
    if (busy) return false;
    busy = true;
    let reloading = false;
    try {
      await deps.persist(code); // 1. save first, so a reload can never lose the choice
      await deps.applyLanguage(code); // 2. UI (and the overlay text) now use the new language
      const result = await deps.syncDirection(code, async () => {
        deps.setSwitching(true); // 3. only reached when the direction really changes and a reload will follow
        await deps.waitForPaint();
      });
      reloading = result === "reloading";
    } finally {
      if (reloading) {
        // The JS runtime is about to be replaced: keep the overlay up and stay locked. If the reload
        // silently does nothing, don't leave the user stuck behind the overlay.
        deps.schedule(() => {
          busy = false;
          deps.setSwitching(false);
        }, safetyMs);
      } else {
        busy = false;
        deps.setSwitching(false);
      }
    }
    return true;
  };
}
