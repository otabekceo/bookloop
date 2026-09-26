import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Platform } from "react-native";
import { useTranslation } from "react-i18next";

import i18n, {
  DEFAULT_LANGUAGE,
  loadStoredLanguage,
  persistLanguage,
} from "./index";
import { syncNativeDirection, waitForBootReloadFallback, waitForOverlayPaint } from "./direction";
import { createLanguageSwitcher } from "./languageSwitch";
import {
  getLanguageMeta,
  isLanguageCode,
  isRTLLanguage,
  type LanguageCode,
} from "./languages";

type LanguageContextType = {
  /** Active language code. */
  language: LanguageCode;
  /** True when the active language uses a right-to-left script. */
  isRTL: boolean;
  /** True until the persisted language has been read from storage. */
  isReady: boolean;
  /** True when the user has never explicitly chosen a language. */
  needsSelection: boolean;
  /** True while switching between LTR and RTL: the "switching language" overlay is up and a reload is coming. */
  isSwitching: boolean;
  /** Change the app language. Persists locally and (when authed) server-side. */
  setLanguage: (code: LanguageCode) => Promise<void>;
  /**
   * Adopt a language coming from the signed-in user's server profile.
   * Unlike `setLanguage`, this does not mark the user as having made an
   * explicit choice and does not write back to the server.
   */
  syncFromServer: (code: string | null | undefined) => Promise<void>;
  /** i18next translate function bound to the active language. */
  t: ReturnType<typeof useTranslation>["t"];
};

const LanguageContext = createContext<LanguageContextType | null>(null);

// Direction handling lives in ./direction.ts: it always stores the wanted direction natively and reloads the
// app only when the direction actually changes (LTR <-> RTL). `isRTL` below is derived from the CURRENT
// language, and the root layout applies it as an explicit `direction` (see app/_layout.tsx).

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const [language, setLanguageState] = useState<LanguageCode>(DEFAULT_LANGUAGE);
  const [isReady, setIsReady] = useState(false);
  const [needsSelection, setNeedsSelection] = useState(false);
  const [isSwitching, setIsSwitching] = useState(false);
  // Guards against writing the language back to the server before the user
  // has actually made a choice.
  const userChoseRef = useRef(false);

  // Bootstrap: read the persisted language before the first meaningful render.
  //
  // FAIL-SAFE: every path through this effect — success, a caught error, or a reload that never lands — must
  // reach the `finally` below, which is the ONLY place `isReady` is set. That is what guarantees the app can
  // never be stuck on the loading screen forever: whatever went wrong, we log it and still render, because a
  // rendered app in the wrong direction (corrected visually by the root `direction` style, and correct again
  // on the next real restart since the native preference is written unconditionally either way) is always
  // safer than an app that never renders at all.
  //
  // TEMPORARY: the console.log calls below are startup diagnostics for a hard-to-reproduce physical-device/
  // Expo Go boot issue. Safe to remove once boot is confirmed reliable; they carry no user data.
  useEffect(() => {
    let cancelled = false;
    let reachedFinally = false;

    console.log("[LanguageBootstrap] START");

    (async () => {
      try {
        const stored = await loadStoredLanguage();
        console.log("[LanguageBootstrap] stored language:", stored);
        if (cancelled) return;

        if (stored) {
          setLanguageState(stored);
          await i18n.changeLanguage(stored);
          console.log("[LanguageBootstrap] i18n changed");

          // Make the native layout direction match the saved language on every start. This also repairs a
          // device left in the wrong direction. Guarded on its own: a throw here (seen on some physical-
          // device/Expo Go native-module setups) must not abort the rest of boot.
          //
          // allowReload=false: BOOT must never trigger a native reload. On some physical-device/Expo Go
          // setups, reloading this early makes Expo Go try to re-download the JS bundle from Metro and can
          // fail with a FATAL native error ("Failed to download remote update") that no JS try/catch can
          // catch or recover from — the opposite of a fail-safe. The native preference is still written
          // (inside syncNativeDirection) so the direction is correct on the next real app start, and the
          // visible layout is already correct right now via the root `direction` style either way. Only the
          // explicit in-app language switch (./languageSwitch.ts) is allowed to reload, once the app — and
          // its connection to the dev server — is already up and stable.
          let result: Awaited<ReturnType<typeof syncNativeDirection>> = "in-sync";
          try {
            result = await syncNativeDirection(isRTLLanguage(stored), undefined, false);
          } catch (e) {
            console.error("[LanguageBootstrap] syncNativeDirection threw:", e);
            result = "pending"; // direction may be visually-only this run; boot still continues
          }
          console.log("[LanguageBootstrap] direction result:", result);

          if (result === "reloading") {
            // Should not happen at boot (allowReload=false above) — kept as defense in depth in case that
            // ever changes, so boot still can't hang even then.
            console.log("[LanguageBootstrap] unexpected reload at boot; fallback started");
            await waitForBootReloadFallback();
            console.log("[LanguageBootstrap] fallback resolved. cancelled:", cancelled);
            if (cancelled) return; // the reload DID land: this context is being torn down
          }
          setNeedsSelection(false);
        } else {
          // Brand-new user: keep the default rendering language but flag that we
          // must show the language picker before login/signup.
          console.log("[LanguageBootstrap] no stored language; needs selection");
          setNeedsSelection(true);
        }
      } catch (e) {
        // Anything unexpected (storage, i18n, or a native-module error not caught above): log it and fall
        // through to `finally` below rather than leaving the app on an infinite spinner.
        console.error("[LanguageBootstrap] bootstrap failed, rendering with current state:", e);
      } finally {
        reachedFinally = true;
        if (cancelled) {
          console.log("[LanguageBootstrap] COMPLETE (cancelled: not setting state)");
        } else {
          console.log("[LanguageBootstrap] setting ready");
          setIsReady(true);
          console.log("[LanguageBootstrap] COMPLETE");
        }
      }
    })();

    return () => {
      cancelled = true;
      if (!reachedFinally) console.log("[LanguageBootstrap] cleanup before completion (cancelled = true)");
    };
  }, []);

  // The switching flow (save first -> apply -> overlay + reload only if LTR <-> RTL changes -> ignore
  // repeated taps) lives in ./languageSwitch.ts; this wires in the real storage, i18n, state and reload.
  const switcherRef = useRef<ReturnType<typeof createLanguageSwitcher> | null>(null);
  if (!switcherRef.current) {
    switcherRef.current = createLanguageSwitcher({
      persist: (code) => persistLanguage(code as LanguageCode),
      applyLanguage: async (code) => {
        await i18n.changeLanguage(code);
        setLanguageState(code as LanguageCode);
        setNeedsSelection(false);
      },
      syncDirection: (code, beforeReload) => syncNativeDirection(isRTLLanguage(code), beforeReload),
      setSwitching: setIsSwitching,
      waitForPaint: waitForOverlayPaint,
      schedule: (fn, ms) => {
        setTimeout(fn, ms);
      },
    });
  }

  const setLanguage = useCallback(async (code: LanguageCode) => {
    userChoseRef.current = true;
    await switcherRef.current!(code);
  }, []);

  const syncFromServer = useCallback(
    async (code: string | null | undefined) => {
      if (!isLanguageCode(code)) return;
      const next: LanguageCode = code;
      // A local explicit choice always wins over a stale server value.
      if (userChoseRef.current) return;
      const stored = await loadStoredLanguage();
      if (stored) return;
      setLanguageState(next);
      setNeedsSelection(false);
      await i18n.changeLanguage(next);
      await persistLanguage(next);
      // allowReload=false: this runs automatically right after sign-in (no user tap), sharing boot's early-
      // lifecycle reload risk (see the boot call above) — not a deliberate switch, so it must not reload.
      await syncNativeDirection(isRTLLanguage(next), undefined, false);
    },
    [],
  );

  // Web only: the browser has no native layout direction to force, so mirror the
  // document itself. Setting `dir`/`lang` on <html> lets the browser's bidi
  // algorithm lay out mixed Arabic + Latin text correctly and flips native
  // scrollbars/overflow. Native platforms are handled in ./direction.ts.
  useEffect(() => {
    if (Platform.OS !== "web" || typeof document === "undefined") return;
    const el = document.documentElement;
    el.dir = isRTLLanguage(language) ? "rtl" : "ltr";
    el.lang = language;
  }, [language]);

  const value = useMemo<LanguageContextType>(
    () => ({
      language,
      isRTL: isRTLLanguage(language),
      isReady,
      needsSelection,
      isSwitching,
      setLanguage,
      syncFromServer,
      t,
    }),
    [language, isReady, needsSelection, isSwitching, setLanguage, syncFromServer, t],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextType {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useLanguage must be used within a LanguageProvider");
  return ctx;
}

/** Convenience hook returning the active language metadata (name, flag, rtl). */
export function useLanguageMeta() {
  const { language } = useLanguage();
  return getLanguageMeta(language);
}

export { isRTLLanguage };
export type { LanguageCode };
