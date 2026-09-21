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
import { syncNativeDirection, waitForOverlayPaint } from "./direction";
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
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = await loadStoredLanguage();
      if (cancelled) return;
      if (stored) {
        setLanguageState(stored);
        await i18n.changeLanguage(stored);
        // Make the native layout direction match the saved language on every start. This also repairs a
        // device left in the wrong direction; if a reload was triggered, wait for it instead of rendering.
        if ((await syncNativeDirection(isRTLLanguage(stored))) === "reloading") return;
        setNeedsSelection(false);
      } else {
        // Brand-new user: keep the default rendering language but flag that we
        // must show the language picker before login/signup.
        setNeedsSelection(true);
      }
      setIsReady(true);
    })();
    return () => {
      cancelled = true;
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
      await syncNativeDirection(isRTLLanguage(next));
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
